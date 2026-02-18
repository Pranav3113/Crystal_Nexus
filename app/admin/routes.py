from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from sqlalchemy import func, distinct, case, or_
from typing import List
from datetime import date, timedelta

from .. import db
from ..models import (
    LeadStatus, LeadSource, ActivityType, AuditLog, LeadActivity, QuoteApproval,
    User, EmployeeProfile,
    Lead, Opportunity, Quote, PaymentCollection,
    PipelineStage, QuoteStatus, Cluster
)
from ..utils import require_perm

admin_bp = Blueprint("admin", __name__, template_folder="../templates")


# =========================================================
# DASHBOARD HELPERS
# =========================================================
def get_team_user_ids(manager_user_id: int, include_self: bool = False) -> List[int]:
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


def recent_opportunities(owner_ids: List[int], limit: int = 8):
    return (
        Opportunity.query
        .filter(Opportunity.owner_id.in_(owner_ids))
        .order_by(Opportunity.created_at.desc())
        .limit(limit)
        .all()
    )


def kpi_leads(owner_ids: List[int]):
    total = (
        db.session.query(func.count(Lead.id))
        .filter(Lead.owner_id.in_(owner_ids))
        .scalar()
    ) or 0

    by_status = (
        db.session.query(LeadStatus.name, func.count(Lead.id))
        .join(LeadStatus, Lead.status_id == LeadStatus.id)
        .filter(Lead.owner_id.in_(owner_ids))
        .group_by(LeadStatus.name)
        .order_by(func.count(Lead.id).desc())
        .all()
    )
    return int(total), by_status


def kpi_clients_from_leads(owner_ids: List[int]) -> int:
    val = (
        db.session.query(func.count(distinct(Lead.client_id)))
        .filter(Lead.owner_id.in_(owner_ids))
        .filter(Lead.client_id.isnot(None))
        .scalar()
    ) or 0
    return int(val)


def kpi_pipeline(owner_ids: List[int]):
    total = (
        db.session.query(func.count(Opportunity.id))
        .filter(Opportunity.owner_id.in_(owner_ids))
        .scalar()
    ) or 0

    value = (
        db.session.query(func.coalesce(func.sum(Opportunity.expected_value), 0))
        .filter(Opportunity.owner_id.in_(owner_ids))
        .scalar()
    ) or 0

    by_stage = (
        db.session.query(PipelineStage.name, func.count(Opportunity.id))
        .join(PipelineStage, Opportunity.stage_id == PipelineStage.id)
        .filter(Opportunity.owner_id.in_(owner_ids))
        .group_by(PipelineStage.name)
        .order_by(func.count(Opportunity.id).desc())
        .all()
    )

    return int(total), float(value), by_stage


def kpi_quotes(owner_ids: List[int]):
    total = (
        db.session.query(func.count(Quote.id))
        .filter(Quote.created_by_id.in_(owner_ids))
        .scalar()
    ) or 0

    amount = (
        db.session.query(func.coalesce(func.sum(Quote.total_amount), 0))
        .filter(Quote.created_by_id.in_(owner_ids))
        .scalar()
    ) or 0

    by_status = (
        db.session.query(QuoteStatus.name, func.count(Quote.id))
        .join(QuoteStatus, Quote.status_id == QuoteStatus.id)
        .filter(Quote.created_by_id.in_(owner_ids))
        .group_by(QuoteStatus.name)
        .order_by(func.count(Quote.id).desc())
        .all()
    )

    return int(total), float(amount), by_status


def kpi_payments(owner_ids: List[int]) -> dict:
    rows = (
        db.session.query(
            PaymentCollection.status,
            func.coalesce(func.sum(PaymentCollection.amount), 0)
        )
        .filter(PaymentCollection.created_by_id.in_(owner_ids))
        .group_by(PaymentCollection.status)
        .all()
    )

    out = {"Pending": 0.0, "Verified": 0.0, "Rejected": 0.0}
    for status, amt in rows:
        out[status] = float(amt)

    out["total"] = float(out["Pending"] + out["Verified"] + out["Rejected"])
    return out


def kpi_outstanding(owner_ids: List[int]) -> float:
    collected_subq = (
        db.session.query(
            PaymentCollection.quote_id.label("q_id"),
            func.coalesce(func.sum(
                case((PaymentCollection.status != "Rejected", PaymentCollection.amount), else_=0)
            ), 0).label("collected_amt")
        )
        .group_by(PaymentCollection.quote_id)
        .subquery()
    )

    outstanding = (
        db.session.query(
            func.coalesce(func.sum(Quote.total_amount - func.coalesce(collected_subq.c.collected_amt, 0)), 0)
        )
        .outerjoin(collected_subq, collected_subq.c.q_id == Quote.id)
        .filter(Quote.created_by_id.in_(owner_ids))
        .scalar()
    ) or 0

    return float(outstanding)


def recent_leads(owner_ids: List[int], limit: int = 8):
    return (
        Lead.query
        .filter(Lead.owner_id.in_(owner_ids))
        .order_by(Lead.created_at.desc())
        .limit(limit)
        .all()
    )


def recent_payments(owner_ids: List[int], limit: int = 8):
    return (
        PaymentCollection.query
        .filter(PaymentCollection.created_by_id.in_(owner_ids))
        .order_by(PaymentCollection.created_at.desc())
        .limit(limit)
        .all()
    )


def kpi_followups(owner_ids, limit: int = 10):
    today = date.today()

    rows = (
        db.session.query(LeadActivity, Lead, ActivityType)
        .join(Lead, LeadActivity.lead_id == Lead.id)
        .outerjoin(ActivityType, LeadActivity.activity_type_id == ActivityType.id)
        .filter(Lead.owner_id.in_(owner_ids))
        .filter(LeadActivity.next_follow_up_at.isnot(None))
        .order_by(LeadActivity.next_follow_up_at.asc())
        .limit(limit)
        .all()
    )

    followups = []
    due_today = 0
    overdue = 0

    for act, lead, atype in rows:
        d = act.next_follow_up_at.date() if act.next_follow_up_at else None
        is_today = (d == today) if d else False
        is_overdue = (d < today) if d else False

        if is_today:
            due_today += 1
        if is_overdue:
            overdue += 1

        followups.append({
            "activity": act,
            "lead": lead,
            "atype": atype,
            "is_today": is_today,
            "is_overdue": is_overdue,
        })

    total = (
        db.session.query(func.count(LeadActivity.id))
        .join(Lead, LeadActivity.lead_id == Lead.id)
        .filter(Lead.owner_id.in_(owner_ids))
        .filter(LeadActivity.next_follow_up_at.isnot(None))
        .scalar()
    ) or 0

    return {
        "total": int(total),
        "due_today": int(due_today),
        "overdue": int(overdue),
        "items": followups
    }


# -------- NEW: Opportunity urgency KPIs --------
def kpi_opportunity_closures(owner_ids):
    today = date.today()
    week_end = today + timedelta(days=7)

    closing_this_week = (
        db.session.query(func.count(Opportunity.id))
        .filter(Opportunity.owner_id.in_(owner_ids))
        .filter(Opportunity.expected_close_date.isnot(None))
        .filter(Opportunity.expected_close_date.between(today, week_end))
        .scalar()
    ) or 0

    overdue = (
        db.session.query(func.count(Opportunity.id))
        .filter(Opportunity.owner_id.in_(owner_ids))
        .filter(Opportunity.expected_close_date.isnot(None))
        .filter(Opportunity.expected_close_date < today)
        .scalar()
    ) or 0

    return {"closing_this_week": int(closing_this_week), "overdue": int(overdue)}


def opportunities_closing_soon(owner_ids, limit=8):
    today = date.today()
    week_end = today + timedelta(days=7)

    return (
        Opportunity.query
        .filter(Opportunity.owner_id.in_(owner_ids))
        .filter(Opportunity.expected_close_date.isnot(None))
        .filter(Opportunity.expected_close_date <= week_end)
        .order_by(Opportunity.expected_close_date.asc())
        .limit(limit)
        .all()
    )


def build_dashboard_context(owner_ids):
    lead_total, lead_by_status = kpi_leads(owner_ids)
    clients = kpi_clients_from_leads(owner_ids)

    opp_total, opp_value, opp_by_stage = kpi_pipeline(owner_ids)

    quote_total, quote_amount, quote_by_status = kpi_quotes(owner_ids)
    payments = kpi_payments(owner_ids)
    outstanding = kpi_outstanding(owner_ids)

    followups = kpi_followups(owner_ids, limit=10)

    opp_closure = kpi_opportunity_closures(owner_ids)
    closing_opps = opportunities_closing_soon(owner_ids, limit=8)

    return {
        "kpi": {
            "lead_total": lead_total,
            "lead_by_status": lead_by_status,
            "clients": clients,

            "opp_total": opp_total,
            "opp_value": opp_value,
            "opp_by_stage": opp_by_stage,
            "opp_closure": opp_closure,

            "quote_total": quote_total,
            "quote_amount": quote_amount,
            "quote_by_status": quote_by_status,

            "payments": payments,
            "outstanding": outstanding,

            "followups": followups,
        },
        "recent": {
            "leads": recent_leads(owner_ids),
            "payments": recent_payments(owner_ids),
            "opportunities": recent_opportunities(owner_ids),
            "closing_opps": closing_opps,
        }
    }


def pending_quote_approvals_for_user(user):
    if not user.is_authenticated:
        return {"count": 0, "items": []}

    q = QuoteApproval.query.filter(QuoteApproval.status == "WAITING")
    role_name = user.role.name if getattr(user, "role", None) else None

    q = q.filter(
        (QuoteApproval.approver_user_id == user.id) |
        ((QuoteApproval.approver_user_id.is_(None)) & (QuoteApproval.approver_role == role_name))
    )

    items = q.order_by(QuoteApproval.created_at.asc()).limit(8).all()
    count = q.count()
    return {"count": int(count), "items": items}


def pending_payment_queue_for_user(user):
    if not user.is_authenticated:
        return {"count": 0}

    if not (user.has_perm("payments.verify") or user.has_perm("payments.admin")):
        return {"count": 0}

    count = PaymentCollection.query.filter(PaymentCollection.status == "Pending").count()
    return {"count": int(count)}


# =========================================================
# DASHBOARD ROUTES
# =========================================================
@admin_bp.route("/dashboard")
@login_required
@require_perm("admin.dashboard.view")
def dashboard():
    my_ids = [current_user.id]
    team_ids = get_team_user_ids(current_user.id, include_self=False)

    my = build_dashboard_context(my_ids)
    team = build_dashboard_context(team_ids) if team_ids else None

    can_team_dashboard = (len(team_ids) > 0) or current_user.has_perm("dashboard.team.view")
    can_team_structure = (len(team_ids) > 0) or current_user.has_perm("team.structure.view")

    quote_approvals = {"count": 0, "items": []}
    if current_user.has_perm("quotes.approve"):
        quote_approvals = pending_quote_approvals_for_user(current_user)

    payment_queue = pending_payment_queue_for_user(current_user)

    return render_template(
        "admin/admin_dashboard.html",
        my=my,
        team=team,
        team_count=len(team_ids),
        can_team_dashboard=can_team_dashboard,
        can_team_structure=can_team_structure,
        quote_approvals=quote_approvals,
        payment_queue=payment_queue,
        date=date  # ✅ IMPORTANT for template comparisons
    )

@admin_bp.route("/team-dashboard")
@login_required
def team_dashboard():
    # manager can access if they have reports OR explicit permission
    team_ids = get_team_user_ids(current_user.id, include_self=False)
    if not team_ids and not current_user.has_perm("dashboard.team.view"):
        abort(403)

    team_users = User.query.filter(User.id.in_(team_ids)).order_by(User.name.asc()).all()

    team_rows = []
    for u in team_users:
        uid = [u.id]
        lead_total, _ = kpi_leads(uid)
        clients = kpi_clients_from_leads(uid)
        payments = kpi_payments(uid)
        outstanding = kpi_outstanding(uid)

        team_rows.append({
            "user": u,
            "leads": lead_total,
            "clients": clients,
            "verified": payments.get("Verified", 0),
            "pending": payments.get("Pending", 0),
            "rejected": payments.get("Rejected", 0),
            "outstanding": outstanding
        })

    totals = build_dashboard_context(team_ids) if team_ids else None

    return render_template(
        "admin/team_dashboard.html",
        team_rows=team_rows,
        totals=totals,
        team_count=len(team_ids)
    )


@admin_bp.route("/team-structure")
@login_required
def team_structure():
    subtree_ids = set(get_team_user_ids(current_user.id, include_self=True))
    if len(subtree_ids) <= 1 and not current_user.has_perm("team.structure.view"):
        abort(403)

    # manager -> reports map
    mgr_map = {}
    profiles = EmployeeProfile.query.all()
    for p in profiles:
        if p.reporting_manager_user_id:
            mgr_map.setdefault(p.reporting_manager_user_id, []).append(p.user_id)

    users = User.query.all()
    users_by_id = {u.id: u for u in users}

    show_all = current_user.has_perm("team.structure.view")

    return render_template(
        "admin/team_structure.html",
        mgr_map=mgr_map,
        users_by_id=users_by_id,
        root_id=current_user.id,
        allowed_ids=subtree_ids,
        show_all=show_all
    )


# =========================================================
# YOUR EXISTING MASTERS + AUDIT LOGS (UNCHANGED)
# =========================================================

# ---------- Lead Status Master ----------
@admin_bp.route("/lead-status-master", methods=["GET", "POST"])
@login_required
@require_perm("masters.manage")
def lead_status_master():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        color = (request.form.get("color") or "secondary").strip()
        sort_order = int(request.form.get("sort_order") or 0)

        if not name:
            flash("Status name is required.", "danger")
            return redirect(url_for("admin.lead_status_master"))

        exists = LeadStatus.query.filter_by(name=name).first()
        if exists:
            flash("Status name already exists.", "warning")
            return redirect(url_for("admin.lead_status_master"))

        db.session.add(LeadStatus(name=name, color=color, sort_order=sort_order, is_active=True))
        db.session.commit()
        flash("Status added ✅", "success")
        return redirect(url_for("admin.lead_status_master"))

    statuses = LeadStatus.query.order_by(LeadStatus.sort_order.asc(), LeadStatus.name.asc()).all()
    return render_template("admin/lead_status_master.html", statuses=statuses)


@admin_bp.route("/lead-status/<int:status_id>/update", methods=["POST"])
@login_required
@require_perm("masters.manage")
def update_lead_status(status_id):
    s = LeadStatus.query.get_or_404(status_id)
    s.name = (request.form.get("name") or "").strip()
    s.color = (request.form.get("color") or "secondary").strip()
    s.sort_order = int(request.form.get("sort_order") or 0)
    s.is_active = True if request.form.get("is_active") == "1" else False

    if not s.name:
        flash("Status name cannot be empty.", "danger")
        return redirect(url_for("admin.lead_status_master"))

    db.session.commit()
    flash("Status updated ✅", "success")
    return redirect(url_for("admin.lead_status_master"))


# ---------- Lead Source Master ----------
@admin_bp.route("/lead-source-master", methods=["GET", "POST"])
@login_required
@require_perm("masters.manage")
def lead_source_master():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        sort_order = int(request.form.get("sort_order") or 0)

        if not name:
            flash("Source name is required.", "danger")
            return redirect(url_for("admin.lead_source_master"))

        exists = LeadSource.query.filter_by(name=name).first()
        if exists:
            flash("Source name already exists.", "warning")
            return redirect(url_for("admin.lead_source_master"))

        db.session.add(LeadSource(name=name, sort_order=sort_order, is_active=True))
        db.session.commit()
        flash("Source added ✅", "success")
        return redirect(url_for("admin.lead_source_master"))

    sources = LeadSource.query.order_by(LeadSource.sort_order.asc(), LeadSource.name.asc()).all()
    return render_template("admin/lead_source_master.html", sources=sources)


@admin_bp.route("/lead-source/<int:source_id>/update", methods=["POST"])
@login_required
@require_perm("masters.manage")
def update_lead_source(source_id):
    s = LeadSource.query.get_or_404(source_id)
    s.name = (request.form.get("name") or "").strip()
    s.sort_order = int(request.form.get("sort_order") or 0)
    s.is_active = True if request.form.get("is_active") == "1" else False

    if not s.name:
        flash("Source name cannot be empty.", "danger")
        return redirect(url_for("admin.lead_source_master"))

    db.session.commit()
    flash("Source updated ✅", "success")
    return redirect(url_for("admin.lead_source_master"))


# ---------- Activity Type Master ----------
@admin_bp.route("/activity-type-master", methods=["GET", "POST"])
@login_required
@require_perm("masters.manage")
def activity_type_master():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        icon = (request.form.get("icon") or "telephone").strip()
        sort_order = int(request.form.get("sort_order") or 0)

        if not name:
            flash("Activity type name is required.", "danger")
            return redirect(url_for("admin.activity_type_master"))

        if ActivityType.query.filter_by(name=name).first():
            flash("Activity type already exists.", "warning")
            return redirect(url_for("admin.activity_type_master"))

        db.session.add(ActivityType(name=name, icon=icon, sort_order=sort_order, is_active=True))
        db.session.commit()
        flash("Activity type added ✅", "success")
        return redirect(url_for("admin.activity_type_master"))

    types = ActivityType.query.order_by(ActivityType.sort_order.asc(), ActivityType.name.asc()).all()
    return render_template("admin/activity_type_master.html", types=types)


@admin_bp.route("/activity-type/<int:type_id>/update", methods=["POST"])
@login_required
@require_perm("masters.manage")
def update_activity_type(type_id):
    t = ActivityType.query.get_or_404(type_id)
    t.name = (request.form.get("name") or "").strip()
    t.icon = (request.form.get("icon") or "telephone").strip()
    t.sort_order = int(request.form.get("sort_order") or 0)
    t.is_active = True if request.form.get("is_active") == "1" else False

    if not t.name:
        flash("Activity type name cannot be empty.", "danger")
        return redirect(url_for("admin.activity_type_master"))

    db.session.commit()
    flash("Activity type updated ✅", "success")
    return redirect(url_for("admin.activity_type_master"))


# ---------- Audit Logs ----------
@admin_bp.route("/audit-logs")
@login_required
@require_perm("admin.audit.view")
def audit_logs():
    logs = AuditLog.query.order_by(AuditLog.performed_at.desc()).limit(500).all()
    return render_template("admin/audit_logs.html", logs=logs)




@admin_bp.route("/cluster-master", methods=["GET", "POST"])
@login_required
@require_perm("clusters.manage")
def cluster_master():
    # ---------- Create / Update ----------
    if request.method == "POST":
        action = (request.form.get("action") or "").strip()

        name = (request.form.get("name") or "").strip()
        head_user_id = (request.form.get("head_user_id") or "").strip()
        is_active = True if (request.form.get("is_active") == "1") else False

        if not name:
            flash("Cluster name is required.", "danger")
            return redirect(url_for("admin.cluster_master"))

        if not head_user_id.isdigit():
            flash("Please select a valid Cluster Head.", "danger")
            return redirect(url_for("admin.cluster_master"))

        head_user_id = int(head_user_id)

        # Ensure head user exists + active
        head_user = User.query.get(head_user_id)
        if not head_user or not head_user.is_active:
            flash("Selected cluster head is invalid/inactive.", "danger")
            return redirect(url_for("admin.cluster_master"))

        # Optional (recommended): allow only BD/AM to be heads
        try:
            role = (head_user.profile.team_role or "").upper()
        except Exception:
            role = ""
        if role not in ("BD", "AM"):
            flash("Cluster head must have Team Role BD or AM.", "warning")

        if action == "create":
            # prevent duplicate cluster name
            existing = Cluster.query.filter(db.func.lower(Cluster.name) == name.lower()).first()
            if existing:
                flash("Cluster with this name already exists.", "danger")
                return redirect(url_for("admin.cluster_master"))

            c = Cluster(name=name, head_user_id=head_user_id, is_active=is_active)
            db.session.add(c)
            db.session.commit()
            flash("Cluster created successfully.", "success")
            return redirect(url_for("admin.cluster_master"))

        elif action == "update":
            cid = (request.form.get("id") or "").strip()
            if not cid.isdigit():
                flash("Invalid cluster id.", "danger")
                return redirect(url_for("admin.cluster_master"))

            c = Cluster.query.get(int(cid))
            if not c:
                flash("Cluster not found.", "danger")
                return redirect(url_for("admin.cluster_master"))

            # prevent duplicate name to another record
            dup = (Cluster.query
                   .filter(db.func.lower(Cluster.name) == name.lower())
                   .filter(Cluster.id != c.id)
                   .first())
            if dup:
                flash("Another cluster with this name already exists.", "danger")
                return redirect(url_for("admin.cluster_master"))

            c.name = name
            c.head_user_id = head_user_id
            c.is_active = is_active
            db.session.commit()
            flash("Cluster updated successfully.", "success")
            return redirect(url_for("admin.cluster_master"))

        elif action == "delete":
            cid = (request.form.get("id") or "").strip()
            if not cid.isdigit():
                flash("Invalid cluster id.", "danger")
                return redirect(url_for("admin.cluster_master"))

            c = Cluster.query.get(int(cid))
            if not c:
                flash("Cluster not found.", "danger")
                return redirect(url_for("admin.cluster_master"))

            # soft delete recommended
            c.is_active = False
            db.session.commit()
            flash("Cluster disabled (soft deleted).", "success")
            return redirect(url_for("admin.cluster_master"))

        flash("Invalid action.", "danger")
        return redirect(url_for("admin.cluster_master"))

    # ---------- List / Filters ----------
    q = (request.args.get("q") or "").strip()
    status = (request.args.get("status") or "").strip()  # all/active/inactive

    qs = Cluster.query.join(User, Cluster.head_user_id == User.id)

    if q:
        qs = qs.filter(or_(
            db.func.lower(Cluster.name).like(f"%{q.lower()}%"),
            db.func.lower(User.name).like(f"%{q.lower()}%"),
            db.func.lower(User.email).like(f"%{q.lower()}%")
        ))

    if status == "active":
        qs = qs.filter(Cluster.is_active == True)
    elif status == "inactive":
        qs = qs.filter(Cluster.is_active == False)

    clusters = qs.order_by(Cluster.id.desc()).all()

    # Head candidates
    users = (User.query
             .join(EmployeeProfile, EmployeeProfile.user_id == User.id)
             .filter(User.is_active == True)
             .order_by(User.name.asc())
             .all())

    return render_template(
        "admin/cluster_master.html",
        clusters=clusters,
        users=users,
        q=q,
        status=status
    )