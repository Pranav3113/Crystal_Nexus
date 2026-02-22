from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from .. import db
from ..utils import require_perm
from ..models import PipelineStage, Opportunity, OpportunityStageHistory, Lead, EmployeeProfile

pipeline_bp = Blueprint("pipeline", __name__, template_folder="../templates")


def _opp_code_next():
    last = Opportunity.query.order_by(Opportunity.id.desc()).first()
    nxt = (last.id + 1) if last else 1
    return f"OP-{nxt:06d}"


def _team_user_ids(manager_user_id: int, include_self: bool = True):
    seen = set([manager_user_id]) if include_self else set()
    stack = [manager_user_id]
    while stack:
        mid = stack.pop()
        rows = (EmployeeProfile.query
                .filter(EmployeeProfile.reporting_manager_user_id == mid)
                .with_entities(EmployeeProfile.user_id)
                .all())
        for (uid,) in rows:
            if uid is not None and uid not in seen:
                seen.add(uid)
                stack.append(uid)
    return list(seen)

def _allowed_owner_ids():
    if current_user.has_perm("pipeline.view_all"):
        return None
    return _team_user_ids(current_user.id, include_self=True)

def _require_opp_access(o: Opportunity):
    allowed = _allowed_owner_ids()
    if allowed is None:
        return
    if o.owner_id not in set(allowed):
        abort(403)


@pipeline_bp.route("/")
@login_required
@require_perm("pipeline.view")
def board():
    stages = PipelineStage.query.filter_by(is_active=True).order_by(PipelineStage.sort_order.asc()).all()

    qs = Opportunity.query.order_by(Opportunity.updated_at.desc())
    allowed = _allowed_owner_ids()
    if allowed is not None:
        qs = qs.filter(Opportunity.owner_id.in_(allowed))

    opps = qs.all()

    grouped = {s.id: [] for s in stages}
    for o in opps:
        if o.stage_id in grouped:
            grouped[o.stage_id].append(o)

    today = datetime.utcnow().date()

    # stage names that should NOT be treated as overdue (adjust as per your masters)
    closed_stage_names = {"Won", "Closed Won", "Lost", "Closed Lost"}

    return render_template(
        "pipeline/board.html",
        stages=stages,
        grouped=grouped,
        today=today,
        closed_stage_names=closed_stage_names
    )

@pipeline_bp.route("/new", methods=["GET", "POST"])
@login_required
@require_perm("pipeline.create")
def create():
    stages = PipelineStage.query.filter_by(is_active=True).order_by(PipelineStage.sort_order.asc()).all()

    # For dropdown selection (optional linking)
    leads = Lead.query.order_by(Lead.created_at.desc()).limit(200).all()

    # If user came from Lead View -> /pipeline/new?lead_id=xx
    lead_id_qs = request.args.get("lead_id")
    lead_from_qs = Lead.query.get(int(lead_id_qs)) if (lead_id_qs and lead_id_qs.isdigit()) else None

    if request.method == "POST":
        exp_close_raw = (request.form.get("expected_close_date") or "").strip()
        expected_close_date = (
            datetime.strptime(exp_close_raw, "%Y-%m-%d").date()
            if exp_close_raw else None
        )
        stage_id = request.form.get("stage_id")
        title = (request.form.get("title") or "").strip()
        company = (request.form.get("company") or "").strip()

        # Lead chosen from dropdown (optional)
        lead_id_form = request.form.get("lead_id")
        selected_lead = Lead.query.get(int(lead_id_form)) if (lead_id_form and lead_id_form.isdigit()) else None

        if not title:
            flash("Opportunity title is required.", "danger")
            # keep selection on failure
            return render_template("pipeline/form.html", stages=stages, leads=leads, lead=selected_lead or lead_from_qs)

        o = Opportunity(
            opp_code=_opp_code_next(),
            title=title,
            company=company,
            contact_name=(request.form.get("contact_name") or "").strip(),
            contact_email=(request.form.get("contact_email") or "").strip().lower(),
            contact_phone=(request.form.get("contact_phone") or "").strip(),
            expected_value=request.form.get("expected_value") or 0,
            expected_close_date=expected_close_date,   # ✅ ADD THIS
            notes=(request.form.get("notes") or "").strip(),
            lead_id=int(lead_id_form) if (lead_id_form and lead_id_form.isdigit()) else None,
            owner_id=current_user.id,
            stage_id=int(stage_id) if (stage_id or "").isdigit() else (stages[0].id if stages else None),
        )

        db.session.add(o)
        db.session.commit()

        db.session.add(OpportunityStageHistory(
            opportunity_id=o.id,
            from_stage_id=None,
            to_stage_id=o.stage_id,
            changed_by_id=current_user.id,
            remark="Created"
        ))
        db.session.commit()

        flash("Opportunity created ✅", "success")
        return redirect(url_for("pipeline.board"))

    # GET: Show form. If came from lead, preselect it.
    return render_template("pipeline/form.html", stages=stages, leads=leads, lead=lead_from_qs)


@pipeline_bp.route("/<int:opp_id>/move", methods=["POST"])
@login_required
@require_perm("pipeline.move")
def move(opp_id):
    o = Opportunity.query.get_or_404(opp_id)
    _require_opp_access(o)
    to_stage_id = request.form.get("to_stage_id")

    if not (to_stage_id and to_stage_id.isdigit()):
        flash("Invalid stage.", "danger")
        return redirect(url_for("pipeline.board"))

    to_stage_id = int(to_stage_id)
    if o.stage_id == to_stage_id:
        return redirect(url_for("pipeline.board"))

    old = o.stage_id
    o.stage_id = to_stage_id
    o.updated_at = datetime.utcnow()

    db.session.commit()

    db.session.add(OpportunityStageHistory(
        opportunity_id=o.id,
        from_stage_id=old,
        to_stage_id=to_stage_id,
        changed_by_id=current_user.id,
        remark=(request.form.get("remark") or "").strip()
    ))
    db.session.commit()

    flash("Stage updated ✅", "success")
    return redirect(url_for("pipeline.board"))