from datetime import date
from calendar import monthrange
from decimal import Decimal

from flask import Blueprint, render_template, request, make_response
from flask_login import login_required

from .. import db
from ..utils import require_perm
from ..models import User, EmployeeProfile, Invoice, Quote, Opportunity, Project, Client, MarginSettings

reports_bp = Blueprint("reports", __name__, template_folder="../templates")


# -------------------------
# Helpers
# -------------------------
def _parse_month(s: str):
    """Accepts YYYY-MM. Defaults to current month. Returns (year, month)."""
    if s:
        s = s.strip()
        try:
            y, m = s.split("-")
            y = int(y); m = int(m)
            if 1 <= m <= 12:
                return y, m
        except Exception:
            pass
    today = date.today()
    return today.year, today.month


def _month_bounds(y: int, m: int):
    start = date(y, m, 1)
    end = date(y, m, monthrange(y, m)[1])
    return start, end


def _cluster_user_ids(head_user_id: int):
    """
    Cluster = head + all reporting tree under him (EmployeeProfile.reporting_manager_user_id).
    """
    seen = set([head_user_id])
    queue = [head_user_id]

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


def _margin_threshold():
    ms = (MarginSettings.query
          .filter(MarginSettings.is_active == True)
          .order_by(MarginSettings.id.desc())
          .first())
    return Decimal(str(ms.threshold_percent)) if ms else Decimal("50.00")


def _render_pdf(html: str, filename: str):
    """
    WeasyPrint PDF render (fallback to HTML if not installed/failed).
    """
    try:
        from weasyprint import HTML
        pdf = HTML(string=html, base_url=request.url_root).write_pdf()
        resp = make_response(pdf)
        resp.headers["Content-Type"] = "application/pdf"
        resp.headers["Content-Disposition"] = f"attachment; filename={filename}"
        return resp
    except Exception:
        resp = make_response(html)
        resp.headers["Content-Type"] = "text/html"
        return resp


def _cluster_heads():
    """
    Cluster heads = users who have at least 1 direct report.
    (Using EmployeeProfile.reporting_manager_user_id values)
    """
    head_ids = (db.session.query(EmployeeProfile.reporting_manager_user_id)
                .filter(EmployeeProfile.reporting_manager_user_id.isnot(None))
                .distinct()
                .all())
    head_ids = [x[0] for x in head_ids if x and x[0]]
    if not head_ids:
        return []
    return (User.query
            .filter(User.id.in_(head_ids), User.is_active == True)
            .order_by(User.name.asc())
            .all())


def _cluster_filter_params():
    """
    returns: (cluster_id_str, cluster_head_user_or_None, allowed_ids_or_None)
    """
    cluster_id = (request.args.get("cluster_id") or "").strip()
    cluster_head = None
    allowed_ids = None
    if cluster_id.isdigit():
        cluster_head = User.query.get(int(cluster_id))
        if cluster_head:
            allowed_ids = _cluster_user_ids(cluster_head.id)
    return cluster_id, cluster_head, allowed_ids


# -------------------------
# Report 1: Productivity (MTD)
# -------------------------
@reports_bp.route("/reports/cluster/productivity", methods=["GET"])
@login_required
@require_perm("admin.dashboard.view")  # can later change to reports.view
def cluster_productivity():
    month_str = (request.args.get("month") or "").strip()
    y, m = _parse_month(month_str)
    start_date, end_date = _month_bounds(y, m)

    days_in_month = (end_date - start_date).days + 1
    days_elapsed = (date.today() - start_date).days + 1
    if days_elapsed < 1:
        days_elapsed = 1
    if days_elapsed > days_in_month:
        days_elapsed = days_in_month

    # Cluster filter (head person)
    cluster_id, cluster_head, allowed_ids = _cluster_filter_params()

    # Users list (either all active OR only allowed cluster)
    u_qs = (User.query
            .outerjoin(EmployeeProfile, EmployeeProfile.user_id == User.id)
            .filter(User.is_active == True))
    if allowed_ids:
        u_qs = u_qs.filter(User.id.in_(allowed_ids))
    users = u_qs.order_by(User.name.asc()).all()

    # Revenue MTD per owner (Opportunity.owner_id)
    rev_qs = (db.session.query(
                Opportunity.owner_id.label("owner_id"),
                db.func.coalesce(db.func.sum(Invoice.total_amount), 0).label("revenue_mtd")
            )
            .join(Quote, Quote.id == Invoice.quote_id)
            .join(Opportunity, Opportunity.id == Quote.opportunity_id)
            .filter(Invoice.invoice_date >= start_date, Invoice.invoice_date <= end_date))

    if allowed_ids:
        rev_qs = rev_qs.filter(Opportunity.owner_id.in_(allowed_ids))

    rows = rev_qs.group_by(Opportunity.owner_id).all()
    revenue_map = {r.owner_id: Decimal(str(r.revenue_mtd or 0)) for r in rows}

    data = []
    total_revenue = Decimal("0")
    total_ctc = Decimal("0")
    red_count = amber_count = green_count = 0

    for u in users:
        monthly_ctc = Decimal(str(u.monthly_ctc or 0))
        team_role = (u.profile.team_role if u.profile else None)  # BD / AM
        role = team_role or "BD"

        required_mult = Decimal("15") if role == "BD" else Decimal("25")
        required_month_revenue = monthly_ctc * required_mult

        rev = revenue_map.get(u.id, Decimal("0"))
        total_revenue += rev
        total_ctc += monthly_ctc

        productivity = (rev / monthly_ctc) if monthly_ctc > 0 else Decimal("0")

        required_mtd = (required_month_revenue * Decimal(days_elapsed) / Decimal(days_in_month)) if days_in_month else Decimal("0")
        run_rate_gap = rev - required_mtd

        status = "Red"
        if required_mtd <= 0:
            status = "Amber" if rev > 0 else "Red"
        else:
            if rev >= required_mtd:
                status = "Green"
            elif rev >= (required_mtd * Decimal("0.80")):
                status = "Amber"
            else:
                status = "Red"

        if status == "Green":
            green_count += 1
        elif status == "Amber":
            amber_count += 1
        else:
            red_count += 1

        data.append({
            "user": u,
            "role": role,
            "monthly_ctc": monthly_ctc,
            "revenue_mtd": rev,
            "productivity": productivity,
            "required_mult": required_mult,
            "required_mtd": required_mtd,
            "run_rate_gap": run_rate_gap,
            "status": status,
        })

    avg_productivity = (total_revenue / total_ctc) if total_ctc > 0 else Decimal("0")

    clusters = _cluster_heads()

    tpl = "reports/cluster_productivity.html"
    ctx = dict(
        month_value=f"{y:04d}-{m:02d}",
        start_date=start_date,
        end_date=end_date,
        days_elapsed=days_elapsed,
        days_in_month=days_in_month,
        total_revenue=total_revenue,
        avg_productivity=avg_productivity,
        green_count=green_count,
        amber_count=amber_count,
        red_count=red_count,
        rows=data,
        clusters=clusters,
        cluster_id=cluster_id,
        cluster=cluster_head,
    )

    if (request.args.get("format") or "").lower() == "pdf":
        html = render_template(tpl, **ctx)
        return _render_pdf(html, f"cluster_productivity_{y:04d}-{m:02d}.pdf")

    return render_template(tpl, **ctx)


# -------------------------
# Report 2: Collections & Aging
# -------------------------
@reports_bp.route("/reports/cluster/collections-aging", methods=["GET"])
@login_required
@require_perm("admin.dashboard.view")
def cluster_collections_aging():
    month_str = (request.args.get("month") or "").strip()
    y, m = _parse_month(month_str)
    start_date, end_date = _month_bounds(y, m)

    today = date.today()

    cluster_id, cluster_head, allowed_ids = _cluster_filter_params()

    qs = (Invoice.query
          .join(Quote, Invoice.quote_id == Quote.id)
          .join(Opportunity, Quote.opportunity_id == Opportunity.id)
          .filter(Invoice.invoice_date >= start_date, Invoice.invoice_date <= end_date)
          .filter(Invoice.status != "Cancelled"))

    # Cluster scope: only invoices where opp owner is in cluster
    if allowed_ids:
        qs = qs.filter(Opportunity.owner_id.in_(allowed_ids))

    invoices = qs.order_by(Invoice.id.desc()).all()

    buckets = {"0-30": [], "31-60": [], "61-90": [], "90+": []}
    total_outstanding = Decimal("0")
    exposure_60 = Decimal("0")

    user_cache = {}

    def _user_name(uid):
        if not uid:
            return "—"
        if uid not in user_cache:
            user_cache[uid] = User.query.get(uid)
        return user_cache[uid].name if user_cache[uid] else "—"

    rows = []
    for inv in invoices:
        remaining = Decimal(str(inv.remaining_amount() or 0))
        if remaining <= 0:
            continue

        due = inv.due_date or inv.invoice_date
        days_outstanding = (today - due).days

        total_outstanding += remaining
        if days_outstanding > 60:
            exposure_60 += remaining

        if days_outstanding <= 30:
            bucket = "0-30"
        elif days_outstanding <= 60:
            bucket = "31-60"
        elif days_outstanding <= 90:
            bucket = "61-90"
        else:
            bucket = "90+"

        opp_owner_id = inv.quote.opportunity.owner_id if inv.quote and inv.quote.opportunity else None
        owner_name = _user_name(opp_owner_id)

        row = {
            "invoice": inv,
            "due_date": due,
            "days_outstanding": days_outstanding,
            "remaining": remaining,
            "bucket": bucket,
            "responsible": owner_name,
        }
        rows.append(row)
        buckets[bucket].append(row)

    top_overdue = sorted(rows, key=lambda r: r["days_outstanding"], reverse=True)[:10]

    clusters = _cluster_heads()

    tpl = "reports/cluster_collections_aging.html"
    ctx = dict(
        month_value=f"{y:04d}-{m:02d}",
        start_date=start_date,
        end_date=end_date,
        total_outstanding=total_outstanding,
        exposure_60=exposure_60,
        buckets=buckets,
        top_overdue=top_overdue,
        rows=rows,
        clusters=clusters,
        cluster_id=cluster_id,
        cluster=cluster_head,
    )

    if (request.args.get("format") or "").lower() == "pdf":
        html = render_template(tpl, **ctx)
        return _render_pdf(html, f"cluster_collections_aging_{y:04d}-{m:02d}.pdf")

    return render_template(tpl, **ctx)


# -------------------------
# Report 3: Margin Quality
# -------------------------
@reports_bp.route("/reports/cluster/margin-quality", methods=["GET"])
@login_required
@require_perm("admin.dashboard.view")
def cluster_margin_quality():
    month_str = (request.args.get("month") or "").strip()
    y, m = _parse_month(month_str)
    start_date, end_date = _month_bounds(y, m)

    client_id = (request.args.get("client_id") or "").strip()
    flag_only = (request.args.get("flag_only") or "").strip()
    responsible = (request.args.get("responsible") or "").strip()

    cluster_id, cluster_head, allowed_ids = _cluster_filter_params()

    threshold = _margin_threshold()

    qs = (Project.query
          .join(Quote, Project.quote_id == Quote.id)
          .join(Opportunity, Quote.opportunity_id == Opportunity.id)
          .outerjoin(Client, Project.client_id == Client.id))

    qs = qs.filter(Project.created_at >= start_date, Project.created_at <= end_date)

    if client_id.isdigit():
        qs = qs.filter(Project.client_id == int(client_id))

    if flag_only == "1":
        qs = qs.filter(Project.margin_percent < threshold)

    if responsible.isdigit():
        qs = qs.filter(Project.account_manager_user_id == int(responsible))

    # Cluster scope rule:
    # If AM is set -> belongs to that AM
    # Else -> belongs to opp owner (BD)
    if allowed_ids:
        qs = qs.filter(
            db.or_(
                Project.account_manager_user_id.in_(allowed_ids),
                db.and_(
                    Project.account_manager_user_id.is_(None),
                    Opportunity.owner_id.in_(allowed_ids)
                )
            )
        )

    rows = qs.order_by(Project.id.desc()).all()

    total_contract = Decimal("0")
    total_cost = Decimal("0")
    flagged_count = 0

    data = []
    for p in rows:
        cv = Decimal(str(p.contract_value or 0))
        tc = Decimal(str(p.total_cost or 0))
        mp = Decimal(str(p.margin_percent or 0))

        total_contract += cv
        total_cost += tc

        is_flag = (mp < threshold)
        if is_flag:
            flagged_count += 1

        resp_name = "—"
        if p.account_manager:
            resp_name = p.account_manager.name
        else:
            opp_owner = p.quote.opportunity.owner if p.quote and p.quote.opportunity else None
            resp_name = opp_owner.name if opp_owner else "—"

        data.append({
            "project": p,
            "contract_value": cv,
            "total_cost": tc,
            "margin_percent": mp,
            "margin_amount": Decimal(str(p.margin_amount or 0)),
            "is_flag": is_flag,
            "responsible": resp_name,
        })

    overall_margin_pct = Decimal("0")
    if total_contract > 0:
        overall_margin_pct = ((total_contract - total_cost) * Decimal("100")) / total_contract

    clients = Client.query.filter(Client.is_active == True).order_by(Client.company_name.asc()).all()
    users = User.query.filter(User.is_active == True).order_by(User.name.asc()).all()
    clusters = _cluster_heads()

    tpl = "reports/cluster_margin_quality.html"
    ctx = dict(
        month_value=f"{y:04d}-{m:02d}",
        start_date=start_date,
        end_date=end_date,
        threshold=threshold,
        client_id=client_id,
        flag_only=flag_only,
        responsible=responsible,
        total_contract=total_contract,
        total_cost=total_cost,
        overall_margin_pct=overall_margin_pct,
        flagged_count=flagged_count,
        clients=clients,
        users=users,
        rows=data,
        clusters=clusters,
        cluster_id=cluster_id,
        cluster=cluster_head,
    )

    if (request.args.get("format") or "").lower() == "pdf":
        html = render_template(tpl, **ctx)
        return _render_pdf(html, f"cluster_margin_quality_{y:04d}-{m:02d}.pdf")

    return render_template(tpl, **ctx)