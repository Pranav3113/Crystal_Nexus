# app/quotes/routes.py

import os
from datetime import datetime
from decimal import Decimal
from io import BytesIO
from decimal import Decimal
from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, jsonify, abort, send_file, current_app
)
from flask_login import login_required, current_user
from sqlalchemy import func, or_, and_, case

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib.enums import TA_RIGHT

from .. import db
from ..utils import require_perm
from ..models import (
    Opportunity, Quote, QuoteItem, QuoteStatus,
    ApprovalRule, ApprovalRuleStep, QuoteApproval,
    Role, User, Client, ClientBranch, BranchContact,
    EmployeeProfile, LeadService, ProformaInvoice, Invoice, Currency
)

quotes_bp = Blueprint("quotes", __name__, template_folder="../templates")


# -------------------------
# VISIBILITY HELPERS (same as Leads)
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

def _calculate_tax_components(q: Quote):
    """
    Returns (cgst, sgst, igst, total_tax)
    GST applies ONLY when currency is INR AND gst flag is enabled.
    """

    # ✅ GST only for INR
    q_currency = (getattr(q, "currency", None) or "INR").strip().upper()
    if q_currency != "INR":
        return Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")
    
    subtotal = _d(q.subtotal or 0, "0") - _d(q.discount or 0, "0")
    if subtotal < 0:
        subtotal = Decimal("0")

    # Need company branch always for GST
    company_branch = q.company_branch
    if not company_branch or not (company_branch.state or "").strip():
        return Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")

    # --- determine customer state ---
    customer_state = ""
    if q.branch_id and q.branch and (q.branch.state or "").strip():
        customer_state = (q.branch.state or "").strip()
    else:
        customer_state = (getattr(q, "billing_state", "") or "").strip()

    # If customer state missing OR GST not applicable -> no tax
    if not customer_state or not getattr(q, "is_gst_applicable", True):
        return Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")

    company_state = (company_branch.state or "").strip().lower()
    customer_state_norm = customer_state.lower()

    gst_rate = Decimal("18")

    if company_state == customer_state_norm:
        cgst = subtotal * Decimal("9") / Decimal("100")
        sgst = subtotal * Decimal("9") / Decimal("100")
        igst = Decimal("0")
    else:
        igst = subtotal * gst_rate / Decimal("100")
        cgst = Decimal("0")
        sgst = Decimal("0")

    total_tax = cgst + sgst + igst
    return cgst, sgst, igst, total_tax

def _can_access_quote(q: Quote) -> bool:
    if current_user.has_perm("quotes.view_all"):
        return True

    allowed_ids = set(_team_user_ids(current_user.id, include_self=True))

    if q.created_by_id == current_user.id:
        return True

    opp_owner = q.opportunity.owner_id if q.opportunity else None
    return (opp_owner in allowed_ids) if opp_owner else False


def _require_quote_access(q: Quote):
    if not _can_access_quote(q):
        abort(403)


def _require_opp_access(opp: Opportunity):
    if current_user.has_perm("quotes.view_all"):
        return
    allowed_ids = set(_team_user_ids(current_user.id, include_self=True))
    if opp.owner_id not in allowed_ids:
        abort(403)


# -------------------------
# Helpers
# -------------------------
def _quote_code_next():
    last = Quote.query.order_by(Quote.id.desc()).first()
    nxt = (last.id + 1) if last else 1
    return f"QT-{nxt:06d}"


def _d(val, default="0"):
    if val is None:
        return Decimal(default)
    s = str(val).strip().replace(",", "")
    if s == "":
        return Decimal(default)
    try:
        return Decimal(s)
    except Exception:
        return Decimal(default)


ALLOWED_BILLING = {"ONETIME", "MONTHLY", "HALF_YEARLY", "ANNUAL"}

BILLING_MULT = {
    "ONETIME": Decimal("1"),
    "MONTHLY": Decimal("1"),
    "HALF_YEARLY": Decimal("6"),
    "ANNUAL": Decimal("12"),
}


def _norm_cycle(v: str) -> str:
    v = (v or "ONETIME").strip().upper()
    return v if v in ALLOWED_BILLING else "ONETIME"


def _recalc_quote(quote: Quote):
    subtotal = Decimal("0")

    for it in quote.items.all():
        qty = _d(it.qty, "0")
        rate = _d(it.rate, "0")

        cycle = _norm_cycle(getattr(it, "billing_cycle", None))
        it.billing_cycle = cycle  # keep normalized in DB

        mult = BILLING_MULT.get(cycle, Decimal("1"))

        # amount is rate * qty * multiplier
        it.amount = (qty * rate * mult)
        subtotal += _d(it.amount, "0")

    quote.subtotal = subtotal
    discount = _d(quote.discount, "0")

    # ✅ Calculate GST automatically
    cgst, sgst, igst, total_tax = _calculate_tax_components(quote)

    quote.tax = total_tax
    quote.cgst = cgst
    quote.sgst = sgst
    quote.igst = igst

    quote.total = subtotal - discount + total_tax

    quote.total_amount = _d(quote.total, "0")


def _get_status(name: str):
    return QuoteStatus.query.filter_by(name=name).first()


def _matching_rules(total_amount: Decimal):
    rules = (ApprovalRule.query
             .filter_by(is_active=True)
             .order_by(ApprovalRule.sort_order.asc(), ApprovalRule.id.asc())
             .all())

    matched = []
    for r in rules:
        if total_amount < _d(r.min_amount, "0"):
            continue
        if r.max_amount is not None and total_amount > _d(r.max_amount, "0"):
            continue
        matched.append(r)
    return matched


def _user_can_act_on(qa: QuoteApproval) -> bool:
    if qa.approver_user_id and qa.approver_user_id == current_user.id:
        return True

    role_name = current_user.role.name if getattr(current_user, "role", None) else ""
    if qa.approver_role and qa.approver_role == role_name:
        return True

    return False


def _activate_next_step_if_any(q: Quote):
    nxt = (q.approvals
           .filter_by(status="WAITING")
           .order_by(QuoteApproval.step_order.asc(), QuoteApproval.id.asc())
           .first())
    if nxt:
        nxt.status = "PENDING"
        db.session.commit()


# -------------------------
# Create quote from opportunity
# -------------------------
@quotes_bp.route("/opportunity/<int:opp_id>/new", methods=["GET", "POST"])
@login_required
@require_perm("quotes.create")
def create_quote(opp_id):
    opp = Opportunity.query.get_or_404(opp_id)
    _require_opp_access(opp)

    draft = _get_status("Draft")

    if request.method == "POST":
        est_closure_raw = request.form.get("estimated_closure_date")
        estimated_closure_date = (
            datetime.strptime(est_closure_raw, "%Y-%m-%d").date()
            if est_closure_raw else None
        )

        q = Quote(
            quote_code=_quote_code_next(),
            version=1,
            opportunity_id=opp.id,
            status_id=draft.id if draft else None,
            created_by_id=current_user.id,
            company_branch_id=current_user.company_branch_id,  # ✅ ADD THIS
            currency=(request.form.get("currency") or "INR").strip().upper(),
            is_gst_applicable=True if (request.form.get("currency") or "INR").strip().upper() == "INR" else False,
            customer_notes=(request.form.get("customer_notes") or "").strip(),
            notes=(request.form.get("notes") or "").strip(),
            discount=Decimal("0"),
            tax=Decimal("0"),
            subtotal=Decimal("0"),
            total=Decimal("0"),
            total_amount=Decimal("0"),
            estimated_closure_date=estimated_closure_date,
        )
        db.session.add(q)
        db.session.commit()

        # ✅ set defaults (billing_cycle/service_id)
        db.session.add(QuoteItem(
            quote_id=q.id,
            item_name="Item 1",
            description="",
            qty=Decimal("1"),
            rate=Decimal("0"),
            amount=Decimal("0"),
            sort_order=1,
            service_id=None,
            billing_cycle="ONETIME",
        ))
        db.session.commit()

        _recalc_quote(q)
        db.session.commit()

        flash("Quote created (Draft) ✅", "success")
        return redirect(url_for("quotes.edit_quote", quote_id=q.id))
    
    currencies = (Currency.query.filter_by(is_active=True)
              .order_by(Currency.sort_order.asc(), Currency.code.asc())
              .all())

    return render_template(
        "quotes/new.html",
        opp=opp,
        currencies=currencies,   # ✅ add
        default_estimated_closure_date=opp.expected_close_date,
        today=datetime.utcnow().date()
    )


# -------------------------
# View quote
# -------------------------
@quotes_bp.route("/<int:quote_id>")
@login_required
@require_perm("quotes.view")
def view_quote(quote_id):
    q = Quote.query.get_or_404(quote_id)
    _require_quote_access(q)

    _recalc_quote(q)
    db.session.commit()

    items = q.items.order_by(QuoteItem.sort_order.asc()).all()

    latest_pi = (ProformaInvoice.query
                 .filter_by(quote_id=q.id)
                 .filter(ProformaInvoice.status != "Cancelled")
                 .order_by(ProformaInvoice.id.desc())
                 .first())

    latest_invoice = (Invoice.query
                      .filter_by(quote_id=q.id)
                      .filter(Invoice.status != "Cancelled")
                      .order_by(Invoice.id.desc())
                      .first())

    currencies = (Currency.query.filter_by(is_active=True)
                  .order_by(Currency.sort_order.asc(), Currency.code.asc())
                  .all())

    
    subtotal_dec = Decimal(q.subtotal or 0)
    discount_dec = Decimal(q.discount or 0)
    taxable_dec = subtotal_dec - discount_dec

    if taxable_dec < 0:
        taxable_dec = Decimal("0")

    return render_template(
        "quotes/view.html",
        q=q,
        items=items,
        today=datetime.utcnow().date(),
        latest_pi=latest_pi,
        latest_invoice=latest_invoice,
        currencies=currencies,
        taxable_dec=taxable_dec,   # ✅ ADD THIS
        Decimal=Decimal  # ✅ add this
    )
# -------------------------
# Edit quote
# -------------------------
@quotes_bp.route("/<int:quote_id>/edit", methods=["GET", "POST"])
@login_required
@require_perm("quotes.edit")
def edit_quote(quote_id):
    q = Quote.query.get_or_404(quote_id)
    _require_quote_access(q)

    if q.status and q.status.name in ("Pending Approval", "Approved", "Sent", "Selected"):
        flash("Quote is locked in current status.", "warning")
        return redirect(url_for("quotes.view_quote", quote_id=q.id))
    
    # Ensure quote has a company branch for GST
    if not q.company_branch_id and getattr(current_user, "company_branch_id", None):
        q.company_branch_id = current_user.company_branch_id


    if request.method == "POST":
        q.currency = (request.form.get("currency") or "INR").strip().upper()
        # ✅ INR-only GST toggle
        if q.currency != "INR":
            q.is_gst_applicable = False
        else:
            q.is_gst_applicable = True if request.form.get("is_gst_applicable") == "1" else False

        q.discount = _d(request.form.get("discount"), "0")
        q.customer_notes = (request.form.get("customer_notes") or "").strip()
        q.notes = (request.form.get("notes") or "").strip()
        q.billing_state = (request.form.get("billing_state") or "").strip() or None
        q.billing_gstin = (request.form.get("billing_gstin") or "").strip() or None
        q.is_gst_applicable = True if request.form.get("is_gst_applicable") == "1" else False
        if not q.company_branch_id and getattr(current_user, "company_branch_id", None):
            q.company_branch_id = current_user.company_branch_id

        est_closure_raw = request.form.get("estimated_closure_date")
        q.estimated_closure_date = (
            datetime.strptime(est_closure_raw, "%Y-%m-%d").date()
            if est_closure_raw else None
        )

        item_ids = request.form.getlist("item_id")
        for idx, item_id in enumerate(item_ids):
            if not str(item_id).isdigit():
                continue

            it = QuoteItem.query.get(int(item_id))
            if not it or it.quote_id != q.id:
                continue

            it.item_name = (request.form.get(f"item_name_{item_id}") or "").strip()
            it.description = (request.form.get(f"item_desc_{item_id}") or "").strip()
            it.qty = _d(request.form.get(f"item_qty_{item_id}"), "0")
            it.rate = _d(request.form.get(f"item_rate_{item_id}"), "0")

            raw_service = (request.form.get(f"item_service_id_{item_id}") or "").strip()
            it.service_id = int(raw_service) if raw_service.isdigit() else None

            it.billing_cycle = _norm_cycle(request.form.get(f"item_billing_cycle_{item_id}") or "ONETIME")
            it.sort_order = idx + 1

        _recalc_quote(q)
        db.session.commit()

        flash("Quote updated ✅", "success")
        return redirect(url_for("quotes.edit_quote", quote_id=q.id))

    _recalc_quote(q)
    db.session.commit()

    items = q.items.order_by(QuoteItem.sort_order.asc()).all()
    services = (LeadService.query
                .filter_by(is_active=True)
                .order_by(LeadService.sort_order.asc(), LeadService.name.asc())
                .all())
    currencies = (Currency.query.filter_by(is_active=True)
              .order_by(Currency.sort_order.asc(), Currency.code.asc())
              .all())

    return render_template("quotes/edit.html", q=q, items=items, services=services, currencies=currencies)


# -------------------------
# Add item
# -------------------------
@quotes_bp.route("/<int:quote_id>/items/add", methods=["GET"])
@login_required
@require_perm("quotes.edit")
def add_item(quote_id):
    q = Quote.query.get_or_404(quote_id)
    _require_quote_access(q)

    if q.status and q.status.name in ("Pending Approval", "Approved", "Sent", "Selected"):
        flash("Quote is locked.", "warning")
        return redirect(url_for("quotes.view_quote", quote_id=q.id))

    db.session.add(QuoteItem(
        quote_id=q.id,
        item_name=f"Item {q.items.count() + 1}",
        description="",
        qty=Decimal("1"),
        rate=Decimal("0"),
        amount=Decimal("0"),
        sort_order=q.items.count() + 1,
        service_id=None,
        billing_cycle="ONETIME",
    ))
    db.session.commit()

    flash("Item added ✅", "success")
    return redirect(url_for("quotes.edit_quote", quote_id=q.id))


# -------------------------
# Auto-save item (AJAX)
# -------------------------
@quotes_bp.route("/<int:quote_id>/items/<int:item_id>/autosave", methods=["POST"])
@login_required
@require_perm("quotes.edit")
def autosave_item(quote_id, item_id):
    q = Quote.query.get_or_404(quote_id)
    _require_quote_access(q)

    it = QuoteItem.query.get_or_404(item_id)
    if it.quote_id != q.id:
        return jsonify({"ok": False, "error": "Invalid item"}), 400

    if q.status and q.status.name in ("Pending Approval", "Approved", "Sent", "Selected"):
        return jsonify({"ok": False, "error": "Quote locked"}), 400

    data = request.get_json(force=True) or {}

    # item fields
    it.item_name = (data.get("item_name") or "").strip()
    it.description = (data.get("description") or "").strip()
    it.qty = _d(data.get("qty"), "0")
    it.rate = _d(data.get("rate"), "0")

    raw_service = data.get("service_id")
    it.service_id = int(raw_service) if str(raw_service).isdigit() else None
    it.billing_cycle = _norm_cycle(data.get("billing_cycle") or it.billing_cycle or "ONETIME")

    # quote fields (global)
    q.discount = _d(data.get("discount"), str(q.discount or 0))

    raw = (data.get("estimated_closure_date") or "").strip()
    q.estimated_closure_date = (
        datetime.strptime(raw, "%Y-%m-%d").date()
        if raw else None
    )

    # ✅ NEW: billing fields so GST can be calculated even for new leads
    q.billing_state = (data.get("billing_state") or "").strip() or None
    q.billing_gstin = (data.get("billing_gstin") or "").strip() or None
    # optional: if you send currency in autosave payload
    if "currency" in data:
        q.currency = (data.get("currency") or q.currency or "INR").strip().upper()

    # ✅ INR-only GST toggle
    if (q.currency or "INR").strip().upper() != "INR":
        q.is_gst_applicable = False
    else:
        q.is_gst_applicable = True if str(data.get("is_gst_applicable", "1")) in ("1", "true", "True") else False

    # ✅ ensure branch exists so GST can compute
    if not q.company_branch_id and getattr(current_user, "company_branch_id", None):
        q.company_branch_id = current_user.company_branch_id

    _recalc_quote(q)
    db.session.commit()

    return jsonify({
        "ok": True,
        "item_amount": str(it.amount or 0),
        "subtotal": str(q.subtotal or 0),
        "discount": str(q.discount or 0),
        "cgst": str(q.cgst or 0),
        "sgst": str(q.sgst or 0),
        "igst": str(q.igst or 0),
        "tax": str(q.tax or 0),
        "total": str(q.total or 0),
    })

# -------------------------
# Request approval
# -------------------------
@quotes_bp.route("/<int:quote_id>/request-approval", methods=["POST"])
@login_required
@require_perm("quotes.request_approval")
def request_approval(quote_id):
    q = Quote.query.get_or_404(quote_id)
    _require_quote_access(q)

    _recalc_quote(q)
    db.session.commit()

    if q.status and q.status.name in ("Pending Approval", "Approved", "Sent", "Selected"):
        flash("Quote is locked. You cannot request approval in current status.", "warning")
        return redirect(url_for("quotes.view_quote", quote_id=q.id))

    rules = _matching_rules(_d(q.total, "0"))

    # ✅ If no rule matched → No approval required → Auto Approve
    if not rules:
        approved = _get_status("Approved")
        if approved:
            q.status_id = approved.id
            db.session.commit()

        flash("No approval required for this amount. Quote auto-approved ✅", "success")
        return redirect(url_for("quotes.view_quote", quote_id=q.id))

    QuoteApproval.query.filter_by(quote_id=q.id).delete(synchronize_session=False)
    db.session.commit()

    global_step = 0
    created_any = False

    for r in rules:
        steps = (ApprovalRuleStep.query
                 .filter_by(rule_id=r.id, is_active=True)
                 .order_by(ApprovalRuleStep.step_order.asc(), ApprovalRuleStep.id.asc())
                 .all())

        if not steps:
            flash(f"Rule '{r.name}' has no steps. Add steps in Approval Rules.", "danger")
            return redirect(url_for("quotes.edit_quote", quote_id=q.id))

        for s in steps:
            global_step += 1
            db.session.add(QuoteApproval(
                quote_id=q.id,
                rule_id=r.id,
                rule_step_id=s.id,
                step_order=global_step,
                approver_role=s.approver_role,
                approver_user_id=s.approver_user_id,
                status="WAITING"
            ))
            created_any = True

    if not created_any:
        flash("No approval steps created. Please check rules/steps.", "danger")
        return redirect(url_for("quotes.edit_quote", quote_id=q.id))

    db.session.commit()

    first = (q.approvals
             .filter_by(status="WAITING")
             .order_by(QuoteApproval.step_order.asc(), QuoteApproval.id.asc())
             .first())
    if first:
        first.status = "PENDING"
        db.session.commit()

    pending = _get_status("Pending Approval")
    if pending:
        q.status_id = pending.id
        db.session.commit()

    flash("Approval requested ✅ (Sequential flow started)", "success")
    return redirect(url_for("quotes.view_quote", quote_id=q.id))


# -------------------------
# Approvals inbox
# -------------------------
@quotes_bp.route("/approvals")
@login_required
@require_perm("quotes.approve")
def approvals_inbox():
    role_name = current_user.role.name if getattr(current_user, "role", None) else ""
    show_waiting = (request.args.get("show_waiting") or "").strip() in ("1", "true", "yes", "on")

    statuses = ["PENDING"]
    if show_waiting:
        statuses.append("WAITING")

    assigned_filter = or_(
        QuoteApproval.approver_user_id == current_user.id,
        and_(QuoteApproval.approver_user_id.is_(None), QuoteApproval.approver_role == role_name),
    )

    status_order = case(
        (QuoteApproval.status == "PENDING", 0),
        (QuoteApproval.status == "WAITING", 1),
        else_=2,
    )

    items = (QuoteApproval.query
             .filter(QuoteApproval.status.in_(statuses))
             .filter(assigned_filter)
             .order_by(status_order.asc(),
                       QuoteApproval.created_at.desc(),
                       QuoteApproval.quote_id.desc(),
                       QuoteApproval.step_order.asc(),
                       QuoteApproval.id.asc())
             .all())

    return render_template("quotes/approvals.html", items=items)


# -------------------------
# Approve / reject
# -------------------------
@quotes_bp.route("/approval/<int:approval_id>/act", methods=["POST"])
@login_required
@require_perm("quotes.approve")
def act_on_approval(approval_id):
    a = QuoteApproval.query.get_or_404(approval_id)
    q = a.quote

    _require_quote_access(q)

    if a.status != "PENDING":
        flash("This approval is not pending.", "warning")
        return redirect(url_for("quotes.approvals_inbox"))

    if not _user_can_act_on(a):
        flash("You are not allowed to approve this step.", "danger")
        return redirect(url_for("quotes.approvals_inbox"))

    decision = request.form.get("decision")
    remark = (request.form.get("remark") or "").strip()

    if decision not in ("APPROVE", "REJECT"):
        flash("Invalid decision.", "danger")
        return redirect(url_for("quotes.approvals_inbox"))

    a.status = "APPROVED" if decision == "APPROVE" else "REJECTED"
    a.remark = remark
    a.acted_by_id = current_user.id
    a.acted_at = datetime.utcnow()
    db.session.commit()

    if decision == "REJECT":
        q.approvals.filter(QuoteApproval.status.in_(["WAITING", "PENDING"])) \
                  .update({"status": "CANCELLED"}, synchronize_session=False)
        db.session.commit()

        rej = _get_status("Rejected")
        if rej:
            q.status_id = rej.id
            db.session.commit()

        flash("Quote rejected ❌", "warning")
        return redirect(url_for("quotes.approvals_inbox"))

    _activate_next_step_if_any(q)

    pending_left = q.approvals.filter_by(status="PENDING").count()
    waiting_left = q.approvals.filter_by(status="WAITING").count()

    if pending_left == 0 and waiting_left == 0:
        appr = _get_status("Approved")
        if appr:
            q.status_id = appr.id
            db.session.commit()
        flash("All approvals completed ✅ Quote Approved", "success")

    return redirect(url_for("quotes.approvals_inbox"))


# =========================================================
# ✅ PROPOSAL WORKFLOW (only after Selected)
# =========================================================
@quotes_bp.route("/<int:quote_id>/proposal", methods=["GET", "POST"])
@login_required
@require_perm("quotes.edit")
def proposal_builder(quote_id):
    q = Quote.query.get_or_404(quote_id)
    _require_quote_access(q)

    if not q.status or q.status.name not in ("Selected", "Sent"):
        flash("Proposal can be viewed only after Quote is Selected (or Sent).", "danger")
        return redirect(url_for("quotes.view_quote", quote_id=q.id))

    if q.status.name == "Sent":
        items = q.items.order_by(QuoteItem.sort_order.asc()).all()
        return render_template("quotes/proposal_builder.html", q=q, items=items, readonly=True)

    if request.method == "POST":
        terms = (request.form.get("proposal_terms") or "").strip()
        if not terms:
            flash("Please add Terms & Conditions.", "danger")
            return redirect(url_for("quotes.proposal_builder", quote_id=q.id))

        q.proposal_terms = terms
        q.proposal_created_at = datetime.utcnow()
        q.proposal_created_by_id = current_user.id
        db.session.commit()

        flash("Proposal saved ✅ You can download and then mark as Sent.", "success")
        return redirect(url_for("quotes.proposal_builder", quote_id=q.id))

    items = q.items.order_by(QuoteItem.sort_order.asc()).all()
    return render_template("quotes/proposal_builder.html", q=q, items=items, readonly=False)


@quotes_bp.route("/<int:quote_id>/proposal/download", methods=["GET"])
@login_required
@require_perm("quotes.view")
def download_proposal(quote_id):
    q = Quote.query.get_or_404(quote_id)
    _require_quote_access(q)

    if not q.status or q.status.name not in ("Selected", "Sent"):
        flash("Proposal download is allowed only for Selected/Sent quotes.", "danger")
        return redirect(url_for("quotes.view_quote", quote_id=q.id))

    if not getattr(q, "proposal_created_at", None):
        flash("Proposal not created yet.", "danger")
        return redirect(url_for("quotes.proposal_builder", quote_id=q.id))

    _recalc_quote(q)
    db.session.commit()

    items = q.items.order_by(QuoteItem.sort_order.asc()).all()

    def _money(val):
        try:
            v = Decimal(str(val or 0))
        except Exception:
            v = Decimal("0")
        return f"{v:,.2f}"

    creator = User.query.get(q.proposal_created_by_id) if getattr(q, "proposal_created_by_id", None) else None
    creator_name = (getattr(creator, "name", None) or "—") if creator else "—"
    creator_email = (getattr(creator, "email", None) or "—") if creator else "—"

    buff = BytesIO()
    doc = SimpleDocTemplate(
        buff,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"Proposal {q.quote_code}",
    )

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="SmallMuted", fontSize=9, leading=12, textColor=colors.grey))
    styles.add(ParagraphStyle(name="H1", fontSize=16, leading=20, spaceAfter=8))
    styles.add(ParagraphStyle(name="Right", fontSize=10, leading=12, alignment=TA_RIGHT))

    story = []

    logo_path = os.path.join(current_app.static_folder, "img", "company_logo.png")
    logo_flowable = None
    if os.path.exists(logo_path):
        logo_flowable = Image(logo_path, width=40 * mm, height=14 * mm)

    header_left = logo_flowable if logo_flowable else Paragraph("<b>Company</b>", styles["Normal"])
    header_right = Paragraph(
        f"<b>PROPOSAL</b><br/>"
        f"<span>Quote:</span> {q.quote_code} &nbsp;&nbsp; <span>Version:</span> {q.version}<br/>"
        f"<span>Generated:</span> {q.proposal_created_at.strftime('%d-%b-%Y %H:%M')}",
        styles["Right"]
    )
    header_tbl = Table([[header_left, header_right]], colWidths=[90 * mm, 90 * mm])
    header_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, -1), 0.6, colors.lightgrey),
    ]))
    story.append(header_tbl)
    story.append(Spacer(1, 10))

    opp = q.opportunity
    story.append(Paragraph("<b>Client / Opportunity</b>", styles["Normal"]))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        f"<b>Opportunity:</b> {(opp.opp_code + ' - ' + opp.title) if opp else '—'}<br/>"
        f"<b>Company:</b> {(opp.company or '—') if opp else '—'}",
        styles["SmallMuted"]
    ))
    story.append(Spacer(1, 10))

    data = [[
        Paragraph("<b>#</b>", styles["Normal"]),
        Paragraph("<b>Item</b>", styles["Normal"]),
        Paragraph("<b>Description</b>", styles["Normal"]),
        Paragraph("<b>Qty</b>", styles["Normal"]),
        Paragraph("<b>Rate</b>", styles["Normal"]),
        Paragraph("<b>Amount</b>", styles["Normal"]),
    ]]

    for idx, it in enumerate(items, start=1):
        data.append([
            Paragraph(str(idx), styles["Normal"]),
            Paragraph(it.item_name or "", styles["Normal"]),
            Paragraph(it.description or "", styles["Normal"]),  # ✅ wraps properly now
            Paragraph(str(it.qty or 0), styles["Normal"]),
            Paragraph(_money(it.rate), styles["Normal"]),
            Paragraph(_money(it.amount), styles["Normal"]),
        ])

    items_tbl = Table(data, colWidths=[10 * mm, 35 * mm, 65 * mm, 14 * mm, 20 * mm, 24 * mm])
    items_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.lightgrey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (3, 1), (5, -1), "RIGHT"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fcfcfd")]),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(items_tbl)
    story.append(Spacer(1, 10))

    subtotal = _d(q.subtotal, "0")
    discount = _d(q.discount, "0")
    tax = _d(q.tax, "0")
    total = _d(q.total_amount, "0")

    totals_data = [
        ["Amount (Subtotal)", _money(subtotal)],
        ["Discount", _money(discount)],
    ]

    if (q.currency or "INR").strip().upper() == "INR":
        totals_data += [
            ["CGST (9%)", _money(q.cgst)],
            ["SGST (9%)", _money(q.sgst)],
            ["IGST (18%)", _money(q.igst)],
            ["Total GST", _money(q.tax)],
        ]

    totals_data += [
        ["Total Amount", _money(total)],
    ]

    totals_tbl = Table(totals_data, colWidths=[60 * mm, 40 * mm], hAlign="RIGHT")
    totals_tbl.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("LINEABOVE", (0, 0), (-1, 0), 0.6, colors.lightgrey),
        ("LINEBELOW", (0, -1), (-1, -1), 0.8, colors.HexColor("#0f172a")),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(totals_tbl)
    story.append(Spacer(1, 12))

    story.append(Paragraph("<b>Proposal created by</b>", styles["Normal"]))
    story.append(Spacer(1, 4))
    story.append(Paragraph(f"{creator_name}<br/>{creator_email}", styles["SmallMuted"]))
    story.append(Spacer(1, 10))

    story.append(Paragraph("<b>Terms & Conditions</b>", styles["Normal"]))
    story.append(Spacer(1, 4))
    terms_lines = [ln.strip() for ln in (q.proposal_terms or "").splitlines() if ln.strip()]
    if not terms_lines:
        story.append(Paragraph("—", styles["SmallMuted"]))
    else:
        for ln in terms_lines:
            story.append(Paragraph(f"• {ln}", styles["SmallMuted"]))

    doc.build(story)
    buff.seek(0)

    filename = f"Proposal_{q.quote_code}.pdf"
    return send_file(buff, as_attachment=True, download_name=filename, mimetype="application/pdf")


# -------------------------
# Mark sent (only if Selected + proposal created)
# -------------------------
@quotes_bp.route("/<int:quote_id>/mark-sent", methods=["POST"])
@login_required
@require_perm("quotes.send")
def mark_sent(quote_id):
    q = Quote.query.get_or_404(quote_id)
    _require_quote_access(q)

    if q.status and q.status.name == "Sent":
        flash("Quote is already marked as Sent.", "info")
        return redirect(url_for("quotes.view_quote", quote_id=q.id))

    if not q.status or q.status.name != "Selected":
        flash("Only Selected quotes can be marked as Sent.", "danger")
        return redirect(url_for("quotes.view_quote", quote_id=q.id))

    if not getattr(q, "proposal_created_at", None):
        flash("Create Proposal first (add T&Cs) before marking as Sent.", "danger")
        return redirect(url_for("quotes.view_quote", quote_id=q.id))

    sent = _get_status("Sent")
    q.status_id = sent.id if sent else q.status_id
    db.session.commit()

    flash("Quote marked as Sent ✅", "success")
    return redirect(url_for("quotes.view_quote", quote_id=q.id))


# -------------------------
# List quotes (latest version only + visibility)
# -------------------------
@quotes_bp.route("/", methods=["GET"])
@login_required
@require_perm("quotes.view")
def list_quotes():
    qtext = (request.args.get("q") or "").strip()
    status_id = (request.args.get("status_id") or "").strip()

    sub = (db.session.query(
        Quote.opportunity_id.label("opp_id"),
        func.max(Quote.version).label("max_ver")
    )
        .group_by(Quote.opportunity_id)
        .subquery())

    qs = (Quote.query
          .join(sub, (Quote.opportunity_id == sub.c.opp_id) & (Quote.version == sub.c.max_ver))
          .outerjoin(QuoteStatus)
          .outerjoin(Opportunity)
          .order_by(Quote.updated_at.desc(), Quote.id.desc()))

    if not current_user.has_perm("quotes.view_all"):
        allowed_ids = _team_user_ids(current_user.id, include_self=True)
        qs = qs.filter(or_(
            Quote.created_by_id == current_user.id,
            Opportunity.owner_id.in_(allowed_ids)
        ))

    if qtext:
        like = f"%{qtext}%"
        qs = qs.filter(
            (Quote.quote_code.like(like)) |
            (Opportunity.company.like(like)) |
            (Opportunity.title.like(like)) |
            (Opportunity.opp_code.like(like))
        )

    if status_id.isdigit():
        qs = qs.filter(Quote.status_id == int(status_id))

    statuses = QuoteStatus.query.order_by(QuoteStatus.name.asc()).all()

    page = request.args.get("page", 1, type=int)
    pagination = qs.paginate(page=page, per_page=15, error_out=False)

    return render_template(
        "quotes/list.html",
        pagination=pagination,
        statuses=statuses,
        qtext=qtext,
        status_id=status_id
    )


# -------------------------
# Create new version of quote
# -------------------------
@quotes_bp.route("/<int:quote_id>/new-version", methods=["POST"])
@login_required
@require_perm("quotes.edit")
def create_new_version(quote_id):
    base = Quote.query.get_or_404(quote_id)
    _require_quote_access(base)

    draft = _get_status("Draft")

    latest = (Quote.query
              .filter(Quote.opportunity_id == base.opportunity_id)
              .order_by(Quote.version.desc())
              .first())

    next_version = (latest.version + 1) if latest else (base.version + 1)
    new_code = _quote_code_next()

    nq = Quote(
        quote_code=new_code,
        version=next_version,
        opportunity_id=base.opportunity_id,
        status_id=draft.id if draft else base.status_id,
        created_by_id=current_user.id,
        company_branch_id=base.company_branch_id,  # ✅ ADD THIS
        currency=base.currency,
        discount=base.discount,
        tax=base.tax,
        customer_notes=base.customer_notes,
        notes=base.notes,
        subtotal=Decimal("0"),
        total=Decimal("0"),
        total_amount=Decimal("0"),
        estimated_closure_date=base.estimated_closure_date,
    )
    db.session.add(nq)
    db.session.flush()

    old_items = base.items.order_by(QuoteItem.sort_order.asc()).all()
    for it in old_items:
        db.session.add(QuoteItem(
            quote_id=nq.id,
            item_name=it.item_name,
            description=it.description,
            qty=_d(it.qty, "0"),
            rate=_d(it.rate, "0"),
            amount=Decimal("0"),
            sort_order=it.sort_order,
            # ✅ copy these too
            service_id=getattr(it, "service_id", None),
            billing_cycle=_norm_cycle(getattr(it, "billing_cycle", None)),
        ))

    db.session.commit()

    _recalc_quote(nq)
    db.session.commit()

    flash(f"New version created ✅ {nq.quote_code} (V{next_version})", "success")
    return redirect(url_for("quotes.edit_quote", quote_id=nq.id))


# -------------------------
# View all versions for an opportunity
# -------------------------
@quotes_bp.route("/opportunity/<int:opp_id>/versions")
@login_required
@require_perm("quotes.view")
def versions_for_opportunity(opp_id):
    opp = Opportunity.query.get_or_404(opp_id)
    _require_opp_access(opp)

    quotes = (Quote.query
              .filter_by(opportunity_id=opp.id)
              .outerjoin(QuoteStatus)
              .order_by(Quote.version.desc(), Quote.updated_at.desc())
              .all())

    return render_template("quotes/versions.html", opp=opp, quotes=quotes)


# -------------------------
# Approval Rules Management
# -------------------------
@quotes_bp.route("/approval-rules", methods=["GET", "POST"])
@login_required
@require_perm("approval_rules.manage")
def approval_rules_master():
    action = request.form.get("action") if request.method == "POST" else None

    if request.method == "POST":
        if action in ("rule_create", "rule_update"):
            rid = request.form.get("rule_id")
            name = (request.form.get("name") or "").strip()
            min_amt = _d(request.form.get("min_amount"), "0")
            max_raw = (request.form.get("max_amount") or "").strip()
            max_amt = None if max_raw == "" else _d(max_raw, "0")
            sort_order = int(request.form.get("sort_order") or 1)
            is_active = True if request.form.get("is_active") == "1" else False

            if not name:
                flash("Rule name is required.", "danger")
                return redirect(url_for("quotes.approval_rules_master"))

            if max_amt is not None and min_amt > max_amt:
                flash("Min Amount cannot be greater than Max Amount.", "danger")
                return redirect(url_for("quotes.approval_rules_master"))

            if action == "rule_create":
                r = ApprovalRule(
                    name=name,
                    min_amount=min_amt,
                    max_amount=max_amt,
                    sort_order=sort_order,
                    is_active=is_active
                )
                db.session.add(r)
                db.session.commit()
                flash("Approval rule created ✅", "success")
                return redirect(url_for("quotes.approval_rules_master"))

            r = ApprovalRule.query.get_or_404(int(rid))
            r.name = name
            r.min_amount = min_amt
            r.max_amount = max_amt
            r.sort_order = sort_order
            r.is_active = is_active
            db.session.commit()
            flash("Approval rule updated ✅", "success")
            return redirect(url_for("quotes.approval_rules_master"))

        if action == "step_add":
            rule_id = int(request.form.get("rule_id"))
            step_order = int(request.form.get("step_order") or 1)
            approver_role = (request.form.get("approver_role") or "").strip() or None
            au = (request.form.get("approver_user_id") or "").strip()
            approver_user_id = int(au) if au.isdigit() else None

            if not approver_role and not approver_user_id:
                flash("Select either Approver Role or Approver User.", "danger")
                return redirect(url_for("quotes.approval_rules_master"))

            db.session.add(ApprovalRuleStep(
                rule_id=rule_id,
                step_order=step_order,
                approver_role=approver_role,
                approver_user_id=approver_user_id,
                is_active=True
            ))
            db.session.commit()
            flash("Step added ✅", "success")
            return redirect(url_for("quotes.approval_rules_master"))

        if action == "step_update":
            step_id = int(request.form.get("step_id"))
            s = ApprovalRuleStep.query.get_or_404(step_id)

            s.step_order = int(request.form.get("step_order") or 1)
            s.approver_role = (request.form.get("approver_role") or "").strip() or None
            au = (request.form.get("approver_user_id") or "").strip()
            s.approver_user_id = int(au) if au.isdigit() else None
            s.is_active = True if request.form.get("is_active") == "1" else False

            if not s.approver_role and not s.approver_user_id:
                flash("Each step must have an approver role or approver user.", "danger")
                return redirect(url_for("quotes.approval_rules_master"))

            db.session.commit()
            flash("Step updated ✅", "success")
            return redirect(url_for("quotes.approval_rules_master"))

        if action == "step_delete":
            step_id = int(request.form.get("step_id"))
            s = ApprovalRuleStep.query.get_or_404(step_id)

            used = QuoteApproval.query.filter_by(rule_step_id=s.id).count()
            if used > 0:
                flash("This step is already used in approvals. Disable it instead of deleting.", "warning")
                return redirect(url_for("quotes.approval_rules_master"))

            db.session.delete(s)
            db.session.commit()
            flash("Step deleted ✅", "success")
            return redirect(url_for("quotes.approval_rules_master"))

    rules = ApprovalRule.query.order_by(ApprovalRule.sort_order.asc(), ApprovalRule.id.asc()).all()
    roles = Role.query.order_by(Role.name.asc()).all()
    users = User.query.order_by(User.name.asc()).all()

    return render_template("quotes/approval_rules.html", rules=rules, roles=roles, users=users)


@quotes_bp.route("/approval-rules/<int:rule_id>/edit", methods=["POST"])
@login_required
@require_perm("approval_rules.manage")
def edit_approval_rule(rule_id):
    flash("This endpoint is deprecated. Edit rules from Approval Rules page.", "info")
    return redirect(url_for("quotes.approval_rules_master"))


@quotes_bp.route("/approval-rules/<int:rule_id>/delete", methods=["POST"])
@login_required
@require_perm("approval_rules.manage")
def delete_approval_rule(rule_id):
    r = ApprovalRule.query.get_or_404(rule_id)

    used = QuoteApproval.query.filter_by(rule_id=r.id).count()
    if used > 0:
        flash("This rule is already used in approvals. Disable it instead of deleting.", "warning")
        return redirect(url_for("quotes.approval_rules_master"))

    db.session.delete(r)
    db.session.commit()
    flash("Approval rule deleted ✅", "success")
    return redirect(url_for("quotes.approval_rules_master"))


# -------------------------
# Convert Approved/Sent Quote -> Client + mark Selected
# -------------------------
@quotes_bp.route("/<int:quote_id>/convert-to-client", methods=["POST"])
@login_required
@require_perm("quotes.edit")
def convert_quote_to_client(quote_id):
    q = Quote.query.get_or_404(quote_id)
    _require_quote_access(q)

    if not q.status or q.status.name not in ("Approved", "Sent"):
        flash("Only Approved/Sent quotes can be converted.", "danger")
        return redirect(url_for("quotes.view_quote", quote_id=q.id))

    if q.client_id:
        flash("This quote is already linked to a client.", "warning")
        return redirect(url_for("quotes.view_quote", quote_id=q.id))

    company_name = (request.form.get("company_name") or "").strip()
    branch_location = (request.form.get("branch_location") or "").strip()
    contact_name = (request.form.get("contact_name") or "").strip()

    if not company_name or not branch_location or not contact_name:
        flash("Company name, branch location, and at least one contact are required.", "danger")
        return redirect(url_for("quotes.view_quote", quote_id=q.id))

    final_payment_raw = request.form.get("final_payment_date")
    if not final_payment_raw:
        flash("Final Payment Date is required when selecting quote.", "danger")
        return redirect(url_for("quotes.view_quote", quote_id=q.id))

    q.final_payment_date = datetime.strptime(final_payment_raw, "%Y-%m-%d").date()

    client = Client(
        company_name=company_name,
        company_industry=(request.form.get("company_industry") or "").strip() or None,
        service=(request.form.get("service") or "").strip() or None,
        client_type=(request.form.get("client_type") or "").strip() or None,
        website=(request.form.get("website") or "").strip() or None,
        reference=(request.form.get("reference") or "").strip() or None,
        pan=(request.form.get("pan") or "").strip() or None,
        client_category=(request.form.get("client_category") or "").strip() or None,
        remarks=(request.form.get("remarks") or "").strip() or None,
        source="LOCAL",
        is_active=True
    )
    db.session.add(client)
    db.session.flush()

    branch = ClientBranch(
        client_id=client.id,
        branch_location=branch_location,
        address=(request.form.get("address") or "").strip() or None,
        city=(request.form.get("city") or "").strip() or None,
        country=(request.form.get("country") or "").strip() or None,
        pin_code=(request.form.get("pin_code") or "").strip() or None,
        state=(request.form.get("state") or "").strip() or (q.billing_state or None),
        gst=(request.form.get("gst") or "").strip() or (q.billing_gstin or None),
        is_active=True
    )
    db.session.add(branch)
    db.session.flush()

    db.session.add(BranchContact(
        branch_id=branch.id,
        name=contact_name,
        phone=(request.form.get("contact_phone") or "").strip() or None,
        email=(request.form.get("contact_email") or "").strip() or None,
        designation=(request.form.get("contact_designation") or "").strip() or None,
        is_primary=True
    ))

    q.client_id = client.id
    q.branch_id = branch.id

    if q.opportunity:
        q.opportunity.client_id = client.id
        q.opportunity.branch_id = branch.id
        if q.opportunity.lead:
            q.opportunity.lead.client_id = client.id
            q.opportunity.lead.branch_id = branch.id

    selected = QuoteStatus.query.filter_by(name="Selected").first()
    if not selected:
        selected = QuoteStatus(name="Selected", sort_order=999, is_active=True)
        db.session.add(selected)
        db.session.flush()

    q.status_id = selected.id

    db.session.commit()
    flash("Converted to Client and marked as Selected ✅", "success")
    return redirect(url_for("quotes.view_quote", quote_id=q.id))


@quotes_bp.route("/proposals/sent", methods=["GET"])
@login_required
@require_perm("quotes.proposals_sent.view")
def sent_proposals():
    sent = QuoteStatus.query.filter_by(name="Sent").first()

    qs = (Quote.query
          .filter(Quote.status_id == (sent.id if sent else -1))
          .filter(Quote.proposal_created_at.isnot(None))
          .order_by(Quote.updated_at.desc(), Quote.id.desc()))

    if not current_user.has_perm("quotes.view_all"):
        allowed_ids = _team_user_ids(current_user.id, include_self=True)
        qs = qs.join(Opportunity).filter(or_(
            Quote.created_by_id == current_user.id,
            Opportunity.owner_id.in_(allowed_ids)
        ))

    items = qs.all()

    # Build latest PI map in ONE query (no per-row queries in template)
    quote_ids = [x.id for x in items]
    latest_pi_by_quote = {}
    if quote_ids:
        rows = (ProformaInvoice.query
                .filter(ProformaInvoice.quote_id.in_(quote_ids))
                .filter(ProformaInvoice.status != "Cancelled")
                .order_by(ProformaInvoice.quote_id.asc(), ProformaInvoice.id.desc())
                .all())
        for pi in rows:
            # keep first encountered = latest due to ordering
            if pi.quote_id not in latest_pi_by_quote:
                latest_pi_by_quote[pi.quote_id] = pi
    
    # Build latest Invoice map in ONE query (no per-row queries in template)
    latest_invoice_by_quote = {}
    if quote_ids:
        rows = (Invoice.query
                .filter(Invoice.quote_id.in_(quote_ids))
                .filter(Invoice.status != "Cancelled")
                .order_by(Invoice.quote_id.asc(), Invoice.id.desc())
                .all())
        for inv in rows:
            if inv.quote_id not in latest_invoice_by_quote:
                latest_invoice_by_quote[inv.quote_id] = inv

    return render_template(
        "quotes/sent_proposals.html",
        items=items,
        latest_pi_by_quote=latest_pi_by_quote,
        latest_invoice_by_quote=latest_invoice_by_quote
    )


@quotes_bp.route("/<int:quote_id>/proposal/confirm", methods=["POST"])
@login_required
@require_perm("quotes.send")
def confirm_proposal(quote_id):
    q = Quote.query.get_or_404(quote_id)
    _require_quote_access(q)

    if not q.status or q.status.name != "Sent":
        flash("Only Sent proposals can be confirmed.", "danger")
        return redirect(url_for("quotes.view_quote", quote_id=q.id))

    if not q.proposal_created_at:
        flash("Proposal not created yet.", "danger")
        return redirect(url_for("quotes.proposal_builder", quote_id=q.id))

    q.proposal_confirmed_at = datetime.utcnow()
    q.proposal_confirmed_by_id = current_user.id
    db.session.commit()

    flash("Proposal confirmed ✅ Payments can now be updated.", "success")
    return redirect(url_for("quotes.sent_proposals"))

@quotes_bp.route("/<int:quote_id>/request-pi", methods=["POST"])
@login_required
@require_perm("proforma.request")
def request_pi(quote_id):
    q = Quote.query.get_or_404(quote_id)
    _require_quote_access(q)

    if not getattr(q, "proposal_confirmed_at", None):
        flash("You can request PI only after Proposal is Confirmed.", "danger")
        return redirect(url_for("quotes.view_quote", quote_id=q.id))

    if not q.status or q.status.name != "Sent":
        flash("You can request PI only after Proposal is Sent.", "danger")
        return redirect(url_for("quotes.view_quote", quote_id=q.id))

    # ✅ if already requested
    if q.pi_request_status == "Pending":
        flash("PI request is already Pending with Finance.", "info")
        return redirect(url_for("quotes.view_quote", quote_id=q.id))

    existing_pi = (ProformaInvoice.query
                   .filter_by(quote_id=q.id)
                   .filter(ProformaInvoice.status != "Cancelled")
                   .order_by(ProformaInvoice.id.desc())
                   .first())
    if existing_pi:
        flash("PI already exists for this quote.", "info")
        return redirect(url_for("proforma.view_pi", pi_id=existing_pi.id))

    note = (request.form.get("pi_request_note") or "").strip() or None

    q.pi_requested_at = datetime.utcnow()
    q.pi_requested_by_id = current_user.id
    q.pi_request_note = note
    q.pi_request_status = "Pending"

    db.session.commit()
    flash("PI request sent to Finance ✅", "success")
    return redirect(url_for("quotes.view_quote", quote_id=q.id))

@quotes_bp.route("/<int:quote_id>/request-invoice", methods=["POST"])
@login_required
@require_perm("invoices.request")
def request_invoice(quote_id):
    q = Quote.query.get_or_404(quote_id)
    _require_quote_access(q)

    # Must be Sent + Proposal Confirmed
    if not q.status or q.status.name != "Sent":
        flash("You can request Invoice only after Proposal is Sent.", "danger")
        return redirect(url_for("quotes.view_quote", quote_id=q.id))

    if not getattr(q, "proposal_confirmed_at", None):
        flash("You can request Invoice only after Proposal is Confirmed.", "danger")
        return redirect(url_for("quotes.view_quote", quote_id=q.id))

    # Must have PI first
    pi = (ProformaInvoice.query
          .filter_by(quote_id=q.id)
          .filter(ProformaInvoice.status != "Cancelled")
          .order_by(ProformaInvoice.id.desc())
          .first())
    if not pi:
        flash("Invoice can be requested only after PI is generated.", "danger")
        return redirect(url_for("quotes.view_quote", quote_id=q.id))

    # If invoice already exists -> redirect
    existing = (Invoice.query
                .filter_by(quote_id=q.id)
                .filter(Invoice.status != "Cancelled")
                .order_by(Invoice.id.desc())
                .first())
    if existing:
        flash("Invoice already exists for this quote.", "info")
        return redirect(url_for("invoices.view_invoice", invoice_id=existing.id))

    # If already pending -> stop
    if getattr(q, "invoice_request_status", None) == "Pending":
        flash("Invoice request is already pending with Finance.", "info")
        return redirect(url_for("quotes.view_quote", quote_id=q.id))

    note = (request.form.get("invoice_request_note") or "").strip() or None

    q.invoice_requested_at = datetime.utcnow()
    q.invoice_requested_by_id = current_user.id
    q.invoice_request_note = note
    q.invoice_request_status = "Pending"
    db.session.commit()

    flash("Invoice request sent to Finance ✅", "success")
    return redirect(url_for("quotes.view_quote", quote_id=q.id))