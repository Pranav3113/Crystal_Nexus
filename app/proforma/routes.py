from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, make_response
from flask_login import login_required, current_user
from sqlalchemy import or_

from app import db
from app.utils import require_perm
from app.models import Quote, ProformaInvoice, QuoteItem, Opportunity, EmployeeProfile, Invoice, InvoicePayment


proforma_bp = Blueprint("proforma", __name__, url_prefix="/proforma", template_folder="../templates")


# -------------------------
# Visibility helpers (same idea as quotes)
# -------------------------
def _team_user_ids(manager_user_id: int, include_self: bool = True):
    seen = set([manager_user_id]) if include_self else set()
    queue = [manager_user_id]

    while queue:
        mid = queue.pop(0)
        rows = (
            db.session.query(EmployeeProfile.user_id)
            .filter(EmployeeProfile.reporting_manager_user_id == mid)
            .all()
        )
        for (uid,) in rows:
            if uid not in seen:
                seen.add(uid)
                queue.append(uid)

    return list(seen)


def _can_access_quote(q: Quote) -> bool:
    if current_user.has_perm("quotes.view_all") or current_user.has_perm("proforma.view_all"):
        return True

    allowed_ids = set(_team_user_ids(current_user.id, include_self=True))

    if q.created_by_id == current_user.id:
        return True

    opp_owner = q.opportunity.owner_id if q.opportunity else None
    return (opp_owner in allowed_ids) if opp_owner else False


def _require_quote_access(q: Quote):
    if not _can_access_quote(q):
        abort(403)


def _pi_no_next():
    last = ProformaInvoice.query.order_by(ProformaInvoice.id.desc()).first()
    nxt = (last.id + 1) if last else 1
    return f"PI-{nxt:06d}"


# -------------------------
# Finance: Pending PI Requests
# -------------------------
@proforma_bp.route("/requests", methods=["GET"])
@login_required
@require_perm("proforma.requests.view")
def pi_requests():
    # Pending requests only
    sent_quotes = (Quote.query
                   .join(Opportunity, Quote.opportunity_id == Opportunity.id)
                   .filter(Quote.pi_request_status == "Pending")
                   .order_by(Quote.pi_requested_at.desc(), Quote.id.desc()))

    # Visibility: Finance should still respect scope (self/team) unless view_all
    if not (current_user.has_perm("quotes.view_all") or current_user.has_perm("proforma.view_all")):
        allowed_ids = _team_user_ids(current_user.id, include_self=True)
        sent_quotes = sent_quotes.filter(or_(
            Quote.created_by_id == current_user.id,
            Opportunity.owner_id.in_(allowed_ids)
        ))

    items = sent_quotes.all()
    return render_template("proforma/pi_requests.html", items=items)


# -------------------------
# Finance: Generate PI (only if requested)
# -------------------------
@proforma_bp.route("/create/<int:quote_id>", methods=["POST"])
@login_required
@require_perm("proforma.generate")
def create_pi_from_quote(quote_id):
    quote = Quote.query.get_or_404(quote_id)
    _require_quote_access(quote)

    if not getattr(quote, "proposal_confirmed_at", None):
        flash("PI can be generated only after Proposal is Confirmed.", "danger")
        return redirect(url_for("quotes.view_quote", quote_id=quote.id))

    if not quote.status or quote.status.name != "Sent":
        flash("PI can be generated only after Proposal is Sent.", "danger")
        return redirect(url_for("quotes.view_quote", quote_id=quote.id))

    # ✅ must be requested first (Sales -> Finance workflow)
    if quote.pi_request_status != "Pending":
        flash("PI can be generated only after Sales requests it (Pending PI Request).", "danger")
        return redirect(url_for("quotes.view_quote", quote_id=quote.id))

    existing = (ProformaInvoice.query
                .filter_by(quote_id=quote.id)
                .filter(ProformaInvoice.status != "Cancelled")
                .order_by(ProformaInvoice.id.desc())
                .first())
    if existing:
        flash("A PI already exists for this quote.", "warning")
        return redirect(url_for("proforma.view_pi", pi_id=existing.id))

    pi = ProformaInvoice(
        pi_no=_pi_no_next(),
        pi_date=datetime.utcnow().date(),
        quote_id=quote.id,
        client_id=quote.client_id,
        client_branch_id=quote.branch_id,
        company_branch_id=getattr(quote, "company_branch_id", None),
        currency=getattr(quote, "currency", "INR"),
        subtotal=quote.subtotal,
        discount=quote.discount,
        tax=quote.tax,
        cgst=getattr(quote, "cgst", 0),
        sgst=getattr(quote, "sgst", 0),
        igst=getattr(quote, "igst", 0),
        total_amount=quote.total_amount,
        notes=getattr(quote, "customer_notes", None),
        terms=getattr(quote, "proposal_terms", None),
        status="Issued",
        created_by_id=current_user.id
    )

    # mark workflow as completed
    quote.pi_request_status = "Approved"
    quote.pi_generated_at = datetime.utcnow()
    quote.pi_generated_by_id = current_user.id

    db.session.add(pi)
    db.session.commit()

    flash("Proforma Invoice generated ✅", "success")
    return redirect(url_for("proforma.view_pi", pi_id=pi.id))


# -------------------------
# View PI
# -------------------------
@proforma_bp.route("/<int:pi_id>", methods=["GET"])
@login_required
@require_perm("proforma.view")
def view_pi(pi_id):
    pi = ProformaInvoice.query.get_or_404(pi_id)

    quote = pi.quote  # ✅ FIX
    _require_quote_access(quote)

    latest_inv = (pi.invoices
                  .filter(Invoice.status != "Cancelled")
                  .order_by(Invoice.id.desc())
                  .first())

    return render_template(
        "proforma/pi_view.html",
        pi=pi,
        quote=quote,
        latest_inv=latest_inv
    )


@proforma_bp.route("/", methods=["GET"])
@login_required
@require_perm("proforma.view")
def list_pi():
    qs = ProformaInvoice.query.join(Quote, ProformaInvoice.quote_id == Quote.id).join(Opportunity, Quote.opportunity_id == Opportunity.id)

    if not (current_user.has_perm("quotes.view_all") or current_user.has_perm("proforma.view_all")):
        allowed_ids = _team_user_ids(current_user.id, include_self=True)
        qs = qs.filter(or_(
            Quote.created_by_id == current_user.id,
            Opportunity.owner_id.in_(allowed_ids)
        ))

    items = qs.order_by(ProformaInvoice.id.desc()).all()
    return render_template("proforma/pi_list.html", items=items)


@proforma_bp.route("/<int:pi_id>/download", methods=["GET"])
@login_required
@require_perm("proforma.view")
def download_pi(pi_id):
    pi = ProformaInvoice.query.get_or_404(pi_id)
    _require_quote_access(pi.quote)

    quote = pi.quote
    items = quote.items.order_by(QuoteItem.sort_order.asc(), QuoteItem.id.asc()).all()

    html = render_template(
        "proforma/pi_pdf.html",
        pi=pi, quote=quote, items=items,
        doc_title="Proforma Invoice",
        doc_no=pi.pi_no,
        doc_date=pi.pi_date,
        doc=pi,
        client=pi.client,
        branch=pi.client_branch,
        company_branch=pi.company_branch,
    )

    try:
        from weasyprint import HTML
        pdf = HTML(string=html, base_url=request.url_root).write_pdf()
        resp = make_response(pdf)
        resp.headers["Content-Type"] = "application/pdf"
        resp.headers["Content-Disposition"] = f"attachment; filename={pi.pi_no}.pdf"
        return resp
    except Exception:
        resp = make_response(html)
        resp.headers["Content-Type"] = "text/html"
        return resp