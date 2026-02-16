from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, make_response
from flask_login import login_required, current_user
from sqlalchemy import or_
from datetime import datetime, timedelta
from app import db
from app.utils import require_perm
from app.models import ProformaInvoice, Invoice, QuoteItem, Quote, Opportunity, EmployeeProfile


invoices_bp = Blueprint("invoices", __name__, url_prefix="/invoices", template_folder="../templates")


# -------------------------
# Visibility helpers (same as quotes/proforma)
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
    if current_user.has_perm("quotes.view_all") or current_user.has_perm("invoices.view_all"):
        return True

    allowed_ids = set(_team_user_ids(current_user.id, include_self=True))

    if q.created_by_id == current_user.id:
        return True

    opp_owner = q.opportunity.owner_id if q.opportunity else None
    return (opp_owner in allowed_ids) if opp_owner else False


def _require_quote_access(q: Quote):
    if not _can_access_quote(q):
        abort(403)


def _invoice_no_next():
    last = Invoice.query.order_by(Invoice.id.desc()).first()
    nxt = (last.id + 1) if last else 1
    return f"INV-{nxt:06d}"


# -------------------------
# Finance: Pending Invoice Requests
# -------------------------
@invoices_bp.route("/requests", methods=["GET"])
@login_required
@require_perm("invoices.requests.view")
def invoice_requests():
    sent_quotes = (Quote.query
                   .join(Opportunity, Quote.opportunity_id == Opportunity.id)
                   .filter(Quote.invoice_request_status == "Pending")
                   .order_by(Quote.invoice_requested_at.desc(), Quote.id.desc()))

    # Visibility: Finance should still respect scope (self/team) unless view_all
    if not (current_user.has_perm("quotes.view_all") or current_user.has_perm("invoices.view_all")):
        allowed_ids = _team_user_ids(current_user.id, include_self=True)
        sent_quotes = sent_quotes.filter(or_(
            Quote.created_by_id == current_user.id,
            Opportunity.owner_id.in_(allowed_ids)
        ))

    items = sent_quotes.all()
    return render_template("invoices/invoice_requests.html", items=items)


# -------------------------
# Finance: Generate Invoice (only if requested)
# -------------------------
@invoices_bp.route("/create-from-pi/<int:pi_id>", methods=["POST"])
@login_required
@require_perm("invoices.generate")
def create_invoice_from_pi(pi_id):
    pi = ProformaInvoice.query.get_or_404(pi_id)
    quote = pi.quote
    _require_quote_access(quote)
    inv_date = datetime.utcnow().date()
    # ✅ Must be requested first (Sales -> Finance workflow)
    if getattr(quote, "invoice_request_status", None) != "Pending":
        flash("Invoice can be generated only after Sales requests it (Pending Invoice Request).", "danger")
        return redirect(url_for("proforma.view_pi", pi_id=pi.id))

    if pi.status == "Cancelled":
        flash("Cannot create invoice from a cancelled PI.", "danger")
        return redirect(url_for("proforma.view_pi", pi_id=pi.id))

    if pi.status != "Issued":
        flash("Invoice can be created only from an Issued PI.", "danger")
        return redirect(url_for("proforma.view_pi", pi_id=pi.id))

    existing = (Invoice.query.filter_by(pi_id=pi.id)
                .filter(Invoice.status != "Cancelled")
                .order_by(Invoice.id.desc())
                .first())
    if existing:
        flash("Invoice already exists for this PI.", "warning")
        return redirect(url_for("invoices.view_invoice", invoice_id=existing.id))

    inv = Invoice(
        invoice_no=_invoice_no_next(),
        invoice_date=datetime.utcnow().date(),
        # NEW: credit terms (default 0)
        credit_days=getattr(pi, "credit_days", None) or 0,
        due_date=inv_date + timedelta(days=(getattr(pi, "credit_days", None) or 0)),
        pi_id=pi.id,
        quote_id=pi.quote_id,
        client_id=pi.client_id,
        client_branch_id=pi.client_branch_id,
        company_branch_id=pi.company_branch_id,
        currency=pi.currency,
        subtotal=pi.subtotal,
        discount=pi.discount,
        tax=pi.tax,
        cgst=pi.cgst,
        sgst=pi.sgst,
        igst=pi.igst,
        total_amount=pi.total_amount,
        notes=pi.notes,
        terms=pi.terms,
        status="Unpaid",
        created_by_id=current_user.id
    )

    # Mark PI converted (your existing behavior)
    pi.status = "Converted"

    # ✅ mark workflow as completed
    quote.invoice_request_status = "Approved"
    quote.invoice_generated_at = datetime.utcnow()
    quote.invoice_generated_by_id = current_user.id

    db.session.add(inv)
    db.session.commit()

    flash("Invoice created ✅", "success")
    return redirect(url_for("invoices.view_invoice", invoice_id=inv.id))


@invoices_bp.route("/<int:invoice_id>")
@login_required
@require_perm("invoices.view")
def view_invoice(invoice_id):
    inv = Invoice.query.get_or_404(invoice_id)
    _require_quote_access(inv.quote)

    if inv.status == "Cancelled":
        flash("Payments cannot be added to a cancelled invoice.", "danger")
        return redirect(url_for("invoices.view_invoice", invoice_id=inv.id))

    collected = inv.collected_amount()
    remaining = inv.remaining_amount()
    return render_template("invoices/invoice_view.html", invoice=inv, collected=collected, remaining=remaining)


@invoices_bp.route("/", methods=["GET"])
@login_required
@require_perm("invoices.view")
def list_invoices():
    qs = (Invoice.query
          .join(Quote, Invoice.quote_id == Quote.id)
          .join(Opportunity, Quote.opportunity_id == Opportunity.id))

    if not (current_user.has_perm("quotes.view_all") or current_user.has_perm("invoices.view_all")):
        allowed_ids = _team_user_ids(current_user.id, include_self=True)
        qs = qs.filter(or_(
            Quote.created_by_id == current_user.id,
            Opportunity.owner_id.in_(allowed_ids)
        ))

    items = qs.order_by(Invoice.id.desc()).all()
    return render_template("invoices/invoice_list.html", items=items)


@invoices_bp.route("/<int:invoice_id>/download", methods=["GET"])
@login_required
@require_perm("invoices.view")
def download_invoice(invoice_id):
    inv = Invoice.query.get_or_404(invoice_id)
    _require_quote_access(inv.quote)

    quote = inv.quote
    items = quote.items.order_by(QuoteItem.sort_order.asc(), QuoteItem.id.asc()).all()

    html = render_template(
        "invoices/invoice_pdf.html",
        invoice=inv, quote=quote, items=items,
        doc_title="Invoice",
        doc_no=inv.invoice_no,
        doc_date=inv.invoice_date,
        doc=inv,
        client=inv.client,
        branch=inv.client_branch,
        company_branch=inv.company_branch,
    )

    try:
        from weasyprint import HTML
        pdf = HTML(string=html, base_url=request.url_root).write_pdf()
        resp = make_response(pdf)
        resp.headers["Content-Type"] = "application/pdf"
        resp.headers["Content-Disposition"] = f"attachment; filename={inv.invoice_no}.pdf"
        return resp
    except Exception:
        resp = make_response(html)
        resp.headers["Content-Type"] = "text/html"
        return resp