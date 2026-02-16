from datetime import datetime
from decimal import Decimal
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from app import db
from app.utils import require_perm
from app.models import Quote, Project, ProjectCost

projects_bp = Blueprint("projects", __name__, url_prefix="/projects", template_folder="../templates")


def _clean(s): return (s or "").strip()


def _project_code_next():
    last = Project.query.order_by(Project.id.desc()).first()
    nxt = (last.id + 1) if last else 1
    return f"PRJ-{nxt:06d}"


from decimal import Decimal
from app.services.margin import get_margin_threshold_percent  # wherever you placed it
    
def recompute_project_margin(project):
    contract = Decimal(str(project.contract_value or 0))
    total_cost = Decimal(str(project.total_cost or 0))

    margin_amt = contract - total_cost
    margin_pct = Decimal("0")
    if contract > 0:
        margin_pct = (margin_amt * Decimal("100")) / contract

    project.margin_amount = margin_amt
    project.margin_percent = margin_pct

    threshold = get_margin_threshold_percent()
    project.margin_flag = (margin_pct < threshold)


@projects_bp.route("/create-from-quote/<int:quote_id>", methods=["POST"])
@login_required
@require_perm("projects.create")
def create_from_quote(quote_id):
    q = Quote.query.get_or_404(quote_id)

    # allow project only after Selected/Sent (your flow)
    if not q.status or q.status.name not in ("Selected", "Sent"):
        flash("Project can be created only after Quote is Selected/Sent.", "danger")
        return redirect(url_for("quotes.view_quote", quote_id=q.id))

    # one project per quote
    if getattr(q, "project", None):
        flash("Project already exists for this quote.", "warning")
        return redirect(url_for("projects.view_project", project_id=q.project.id))

    p = Project(
        project_code=_project_code_next(),
        name=f"{q.opportunity.title} ({q.quote_code})",
        quote_id=q.id,
        client_id=q.client_id,
        branch_id=q.branch_id,
        currency=q.currency or "INR",
        contract_value=q.total_amount or q.total or 0,
        created_at=datetime.utcnow()
    )
    recompute_project_margin(p)

    db.session.add(p)
    db.session.commit()

    flash("Project created ✅", "success")
    return redirect(url_for("projects.view_project", project_id=p.id))


@projects_bp.route("/<int:project_id>", methods=["GET", "POST"])
@login_required
@require_perm("projects.view")
def view_project(project_id):
    p = Project.query.get_or_404(project_id)

    if request.method == "POST":
        # add cost
        if not current_user.has_perm("projects.cost.add"):
            abort(403)

        cost_head = _clean(request.form.get("cost_head"))
        vendor = _clean(request.form.get("vendor_name"))
        notes = _clean(request.form.get("notes"))
        amt = _clean(request.form.get("amount")) or "0"
        cost_date = request.form.get("cost_date") or datetime.utcnow().date().isoformat()

        try:
            amt_dec = Decimal(amt)
        except Exception:
            amt_dec = Decimal("0")

        if not cost_head or amt_dec <= 0:
            flash("Cost Head and Amount (>0) are required.", "danger")
            return redirect(url_for("projects.view_project", project_id=p.id))

        c = ProjectCost(
            project_id=p.id,
            cost_date=datetime.fromisoformat(cost_date).date(),
            cost_head=cost_head,
            vendor_name=vendor or None,
            amount=amt_dec,
            notes=notes or None,
            created_by_id=current_user.id
        )
        db.session.add(c)

        # update totals
        p.total_cost = (Decimal(str(p.total_cost or 0)) + amt_dec)
        recompute_project_margin(p)

        db.session.commit()
        flash("Cost added ✅", "success")
        return redirect(url_for("projects.view_project", project_id=p.id))

    costs = p.costs.order_by(ProjectCost.cost_date.desc(), ProjectCost.id.desc()).all()
    return render_template("projects/project_view.html", project=p, costs=costs)


@projects_bp.route("/cost/<int:cost_id>/delete", methods=["POST"])
@login_required
@require_perm("projects.cost.delete")
def delete_cost(cost_id):
    c = ProjectCost.query.get_or_404(cost_id)
    p = c.project

    amt_dec = Decimal(str(c.amount or 0))
    db.session.delete(c)

    # recompute total_cost safely from DB (best)
    total = db.session.query(db.func.coalesce(db.func.sum(ProjectCost.amount), 0)).filter(ProjectCost.project_id == p.id).scalar() or 0
    p.total_cost = Decimal(str(total))
    recompute_project_margin(p)

    db.session.commit()
    flash("Cost deleted ✅", "success")
    return redirect(url_for("projects.view_project", project_id=p.id))