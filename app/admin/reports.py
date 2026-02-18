from datetime import date, timedelta
from calendar import monthrange
from decimal import Decimal

from flask import Blueprint, render_template, request, make_response
from flask_login import login_required

from .. import db
from ..utils import require_perm
from ..models import (
    User, EmployeeProfile,
    Lead, LeadSource,
    Opportunity, PipelineStage,
    Quote, QuoteStatus,
    Invoice,
    Project, Client,
    MarginSettings,
    Cluster,
)

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


def _clusters():
    """Dropdown list"""
    return (Cluster.query
            .filter(Cluster.is_active == True)
            .order_by(Cluster.name.asc())
            .all())


def _cluster_filter_params():
    """
    returns: (cluster_id_str, cluster_obj_or_None, allowed_user_ids_or_None)
    cluster_id is Cluster.id
    """
    cluster_id = (request.args.get("cluster_id") or "").strip()
    cluster = None
    allowed_ids = None

    if cluster_id.isdigit():
        cluster = Cluster.query.get(int(cluster_id))
        if cluster and cluster.head_user_id:
            allowed_ids = _cluster_user_ids(cluster.head_user_id)

    return cluster_id, cluster, allowed_ids


def _role(u: User):
    tr = ""
    try:
        tr = (u.profile.team_role or "").strip()
    except Exception:
        tr = ""

    tr_u = tr.upper().replace("_", " ").replace("-", " ").strip()

    if tr_u in ("AM", "ACCOUNT MANAGER", "ACCOUNT MANAGEMENT"):
        return "AM"
    if tr_u in ("BD", "BUSINESS DEVELOPMENT", "SALES"):
        return "BD"

    # unknown / not set
    return ""


def _qualified_stage_filter():
    """
    Define "Qualified opportunity" heuristics.
    If you want stricter definition later, update here only.
    """
    # Prefer probability >= 50 OR stage name contains keywords.
    keywords = ("QUAL", "PROPOS", "NEGOT", "WON")
    return db.or_(
        PipelineStage.probability >= 50,
        db.func.upper(PipelineStage.name).like("%QUAL%"),
        db.func.upper(PipelineStage.name).like("%PROPOS%"),
        db.func.upper(PipelineStage.name).like("%NEGOT%"),
        db.func.upper(PipelineStage.name).like("%WON%"),
    )


def _safe_dec(v):
    try:
        return Decimal(str(v or 0))
    except Exception:
        return Decimal("0")


# -------------------------
# Report 1: Productivity (MTD)
# -------------------------
@reports_bp.route("/reports/cluster/productivity", methods=["GET"])
@login_required
@require_perm("admin.dashboard.view")
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

    cluster_id, cluster, allowed_ids = _cluster_filter_params()

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
    revenue_map = {r.owner_id: _safe_dec(r.revenue_mtd) for r in rows}

    data = []
    total_revenue = Decimal("0")
    total_ctc = Decimal("0")
    red_count = amber_count = green_count = 0

    for u in users:
        monthly_ctc = _safe_dec(u.monthly_ctc)
        role = _role(u)

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

    clusters = _clusters()
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
        cluster=cluster,
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

    cluster_id, cluster, allowed_ids = _cluster_filter_params()

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
        remaining = _safe_dec(inv.remaining_amount() or 0)
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

    clusters = _clusters()
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
        cluster=cluster,
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

    cluster_id, cluster, allowed_ids = _cluster_filter_params()
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
        cv = _safe_dec(p.contract_value)
        tc = _safe_dec(p.total_cost)
        mp = _safe_dec(p.margin_percent)

        total_contract += cv
        total_cost += tc

        is_flag = (mp < threshold)
        if is_flag:
            flagged_count += 1

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
            "margin_amount": _safe_dec(p.margin_amount),
            "is_flag": is_flag,
            "responsible": resp_name,
        })

    overall_margin_pct = Decimal("0")
    if total_contract > 0:
        overall_margin_pct = ((total_contract - total_cost) * Decimal("100")) / total_contract

    clients = Client.query.filter(Client.is_active == True).order_by(Client.company_name.asc()).all()
    users = User.query.filter(User.is_active == True).order_by(User.name.asc()).all()
    clusters = _clusters()

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
        cluster=cluster,
    )

    if (request.args.get("format") or "").lower() == "pdf":
        html = render_template(tpl, **ctx)
        return _render_pdf(html, f"cluster_margin_quality_{y:04d}-{m:02d}.pdf")

    return render_template(tpl, **ctx)


# -------------------------
# Report 4: Pipeline vs Conversion (BD Only)
# -------------------------
@reports_bp.route("/reports/cluster/pipeline-conversion", methods=["GET"])
@login_required
@require_perm("admin.dashboard.view")
def cluster_pipeline_conversion():
    month_str = (request.args.get("month") or "").strip()
    y, m = _parse_month(month_str)
    start_date, end_date = _month_bounds(y, m)

    cluster_id, cluster, allowed_ids = _cluster_filter_params()

    # BD users in scope
    u_qs = (User.query
            .join(EmployeeProfile, EmployeeProfile.user_id == User.id)
            .filter(User.is_active == True))
    if allowed_ids:
        u_qs = u_qs.filter(User.id.in_(allowed_ids))
    users = u_qs.order_by(User.name.asc()).all()
    bd_users = [u for u in users if _role(u) == "BD"]
    # Optional fallback: if nobody tagged as BD, you can include all users
    # bd_users = bd_users or users
    bd_ids = [u.id for u in bd_users]
    if not bd_ids:
        bd_ids = [-1]  # safe empty

    # Leads generated (group by source)
    lead_rows = (db.session.query(
                    LeadSource.name.label("source"),
                    db.func.count(Lead.id).label("cnt")
                 )
                 .outerjoin(LeadSource, Lead.source_id == LeadSource.id)
                 .filter(Lead.created_at >= start_date, Lead.created_at <= end_date)
                 .filter(Lead.owner_id.in_(bd_ids))
                 .group_by(LeadSource.name)
                 .all())
    leads_by_source = [{"source": (r.source or "Unknown"), "count": int(r.cnt or 0)} for r in lead_rows]
    total_leads = sum(x["count"] for x in leads_by_source)

    # Opportunities created (this month)
    opp_qs = (Opportunity.query
              .join(PipelineStage, Opportunity.stage_id == PipelineStage.id)
              .filter(Opportunity.created_at >= start_date, Opportunity.created_at <= end_date)
              .filter(Opportunity.owner_id.in_(bd_ids)))
    total_opps = opp_qs.count()

    qualified_opps = (opp_qs.filter(_qualified_stage_filter())).count()

    # Won value (use invoices as source of truth)
    won_rows = (db.session.query(
                    db.func.count(db.distinct(Opportunity.id)).label("won_count"),
                    db.func.coalesce(db.func.sum(Invoice.total_amount), 0).label("won_value"),
                    db.func.min(Invoice.invoice_date).label("min_inv_date"),
                )
                .join(Quote, Quote.id == Invoice.quote_id)
                .join(Opportunity, Opportunity.id == Quote.opportunity_id)
                .filter(Invoice.invoice_date >= start_date, Invoice.invoice_date <= end_date)
                .filter(Opportunity.owner_id.in_(bd_ids))
                .all())
    won_count = int(won_rows[0].won_count or 0) if won_rows else 0
    won_value = _safe_dec(won_rows[0].won_value) if won_rows else Decimal("0")

    conversion_pct = Decimal("0")
    if qualified_opps > 0:
        conversion_pct = (Decimal(won_count) * Decimal("100")) / Decimal(qualified_opps)

    avg_deal_size = Decimal("0")
    if won_count > 0:
        avg_deal_size = won_value / Decimal(won_count)

    # Sales cycle days (avg: invoice_date - opportunity.created_at)
    cycle_rows = (db.session.query(
                    Opportunity.id.label("opp_id"),
                    Opportunity.created_at.label("opp_created"),
                    db.func.min(Invoice.invoice_date).label("first_invoice_date")
                 )
                 .join(Quote, Quote.opportunity_id == Opportunity.id)
                 .join(Invoice, Invoice.quote_id == Quote.id)
                 .filter(Invoice.invoice_date >= start_date, Invoice.invoice_date <= end_date)
                 .filter(Opportunity.owner_id.in_(bd_ids))
                 .group_by(Opportunity.id, Opportunity.created_at)
                 .all())

    cycle_days = []
    for r in cycle_rows:
        if r.first_invoice_date and r.opp_created:
            try:
                cd = (r.first_invoice_date - r.opp_created.date()).days
                if cd >= 0:
                    cycle_days.append(cd)
            except Exception:
                pass

    avg_cycle_days = (sum(cycle_days) / len(cycle_days)) if cycle_days else 0

    clusters = _clusters()
    tpl = "reports/cluster_pipeline_conversion.html"
    ctx = dict(
        month_value=f"{y:04d}-{m:02d}",
        start_date=start_date,
        end_date=end_date,

        clusters=clusters,
        cluster_id=cluster_id,
        cluster=cluster,

        bd_users=bd_users,
        leads_by_source=leads_by_source,
        total_leads=total_leads,

        total_opps=total_opps,
        qualified_opps=qualified_opps,

        won_count=won_count,
        won_value=won_value,
        conversion_pct=conversion_pct,
        avg_deal_size=avg_deal_size,
        avg_cycle_days=avg_cycle_days,
    )

    if (request.args.get("format") or "").lower() == "pdf":
        html = render_template(tpl, **ctx)
        return _render_pdf(html, f"cluster_pipeline_conversion_{y:04d}-{m:02d}.pdf")

    return render_template(tpl, **ctx)


# -------------------------
# Report 5: Account Health (AM Only)
# -------------------------
@reports_bp.route("/reports/cluster/account-health", methods=["GET"])
@login_required
@require_perm("admin.dashboard.view")
def cluster_account_health():
    month_str = (request.args.get("month") or "").strip()
    y, m = _parse_month(month_str)
    start_date, end_date = _month_bounds(y, m)

    cluster_id, cluster, allowed_ids = _cluster_filter_params()

    # Reference date for "last 30/60/90"
    ref_date = min(date.today(), end_date)

    u_qs = (User.query
            .join(EmployeeProfile, EmployeeProfile.user_id == User.id)
            .filter(User.is_active == True))
    if allowed_ids:
        u_qs = u_qs.filter(User.id.in_(allowed_ids))
    users = u_qs.order_by(User.name.asc()).all()
    am_users = [u for u in users if _role(u) == "AM"]
    am_ids = [u.id for u in am_users]
    if not am_ids:
        am_ids = [-1]

    # Clients assigned to AM: via Project.account_manager_user_id
    # (If later you add Client.account_manager_user_id, we can switch to that.)
    client_rows = (db.session.query(
                        Project.account_manager_user_id.label("am_id"),
                        Project.client_id.label("client_id"),
                        db.func.max(Project.id).label("any_project")
                   )
                   .filter(Project.account_manager_user_id.in_(am_ids))
                   .filter(Project.client_id.isnot(None))
                   .group_by(Project.account_manager_user_id, Project.client_id)
                   .all())

    # build AM -> client_ids
    am_to_clients = {}
    for r in client_rows:
        am_to_clients.setdefault(int(r.am_id), set()).add(int(r.client_id))

    # Preload clients
    all_client_ids = sorted({cid for s in am_to_clients.values() for cid in s})
    clients_map = {}
    if all_client_ids:
        cl = Client.query.filter(Client.id.in_(all_client_ids)).all()
        clients_map = {c.id: c for c in cl}

    def _sum_rev(client_id, d_from, d_to):
        v = (db.session.query(db.func.coalesce(db.func.sum(Invoice.total_amount), 0))
             .filter(Invoice.client_id == client_id)
             .filter(Invoice.invoice_date >= d_from, Invoice.invoice_date <= d_to)
             .filter(Invoice.status != "Cancelled")
             .scalar()) or 0
        return _safe_dec(v)

    def _last_invoice_date(client_id):
        return (db.session.query(db.func.max(Invoice.invoice_date))
                .filter(Invoice.client_id == client_id)
                .filter(Invoice.status != "Cancelled")
                .scalar())

    # windows
    d30_from = ref_date - timedelta(days=29)
    d60_from = ref_date - timedelta(days=59)
    d90_from = ref_date - timedelta(days=89)
    prev30_from = ref_date - timedelta(days=59)
    prev30_to = ref_date - timedelta(days=30)

    rows = []
    active_clients = 0
    dormant_risk = 0
    inactive_accounts = 0

    for am in am_users:
        cids = sorted(list(am_to_clients.get(am.id, set())))
        for cid in cids:
            c = clients_map.get(cid)
            if not c:
                continue

            rev_30 = _sum_rev(cid, d30_from, ref_date)
            rev_60 = _sum_rev(cid, d60_from, ref_date)
            rev_90 = _sum_rev(cid, d90_from, ref_date)

            prev_30 = _sum_rev(cid, prev30_from, prev30_to)
            trend = "Stable"
            if rev_30 > (prev_30 * Decimal("1.10")):
                trend = "Up"
            elif rev_30 < (prev_30 * Decimal("0.90")):
                trend = "Down"

            last_inv = _last_invoice_date(cid)

            # health status
            status = "Active"
            if last_inv is None:
                status = "Inactive"
            else:
                gap = (ref_date - last_inv).days
                if gap > 90:
                    status = "Inactive"
                elif gap > 60:
                    status = "Dormant Risk"
                else:
                    status = "Active"

            if status == "Active":
                active_clients += 1
            elif status == "Dormant Risk":
                dormant_risk += 1
            else:
                inactive_accounts += 1

            rows.append({
                "am": am,
                "client": c,
                "rev_30": rev_30,
                "rev_60": rev_60,
                "rev_90": rev_90,
                "trend": trend,
                "last_invoice_date": last_inv,
                "status": status,
            })

    clusters = _clusters()
    tpl = "reports/cluster_account_health.html"
    ctx = dict(
        month_value=f"{y:04d}-{m:02d}",
        start_date=start_date,
        end_date=end_date,
        ref_date=ref_date,

        clusters=clusters,
        cluster_id=cluster_id,
        cluster=cluster,

        rows=rows,
        active_clients=active_clients,
        dormant_risk=dormant_risk,
        inactive_accounts=inactive_accounts,
    )

    if (request.args.get("format") or "").lower() == "pdf":
        html = render_template(tpl, **ctx)
        return _render_pdf(html, f"cluster_account_health_{y:04d}-{m:02d}.pdf")

    return render_template(tpl, **ctx)