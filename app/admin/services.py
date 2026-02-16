from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required
from sqlalchemy import func

from .. import db
from ..audit import log_audit
from ..utils import require_perm
from ..models import LeadService, Lead


admin_services_bp = Blueprint(
    "admin_services",
    __name__,
    template_folder="../templates"
)


@admin_services_bp.route("/lead-services", methods=["GET", "POST"])
@login_required
@require_perm("lead_services.manage")
def lead_services_master():
    q = (request.args.get("q") or "").strip()
    edit_id = (request.args.get("edit_id") or "").strip()

    query = LeadService.query
    if q:
        like = f"%{q}%"
        query = query.filter(LeadService.name.ilike(like))

    items = query.order_by(LeadService.sort_order.asc(), LeadService.name.asc()).all()

    edit_item = None
    if edit_id.isdigit():
        edit_item = LeadService.query.get(int(edit_id))

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()

        if action == "create":
            name = (request.form.get("name") or "").strip()
            sort_order = int(request.form.get("sort_order") or 0)
            is_active = True if request.form.get("is_active") == "1" else False

            if not name:
                flash("Service name is required.", "danger")
                return redirect(url_for("admin_services.lead_services_master"))

            exists = LeadService.query.filter(func.lower(LeadService.name) == name.lower()).first()
            if exists:
                flash("Service already exists.", "warning")
                return redirect(url_for("admin_services.lead_services_master"))

            obj = LeadService(name=name, sort_order=sort_order, is_active=is_active)
            db.session.add(obj)
            db.session.commit()

            log_audit("LeadService", obj.id, "CREATE")
            flash("Service added ✅", "success")
            return redirect(url_for("admin_services.lead_services_master"))

        if action == "update":
            sid = (request.form.get("id") or "").strip()
            if not sid.isdigit():
                flash("Invalid service.", "danger")
                return redirect(url_for("admin_services.lead_services_master"))

            obj = LeadService.query.get(int(sid))
            if not obj:
                flash("Service not found.", "danger")
                return redirect(url_for("admin_services.lead_services_master"))

            old_name = obj.name
            obj.name = (request.form.get("name") or "").strip()
            obj.sort_order = int(request.form.get("sort_order") or 0)
            obj.is_active = True if request.form.get("is_active") == "1" else False

            db.session.commit()

            if old_name != obj.name:
                log_audit("LeadService", obj.id, "UPDATE", "name", old_name, obj.name)

            flash("Service updated ✅", "success")
            return redirect(url_for("admin_services.lead_services_master"))

        flash("Invalid action.", "danger")
        return redirect(url_for("admin_services.lead_services_master"))

    return render_template(
        "admin/lead_service_master.html",
        items=items,
        edit_item=edit_item,
        q=q
    )


@admin_services_bp.route("/lead-services/<int:service_id>/delete", methods=["POST"])
@login_required
@require_perm("lead_services.manage")
def delete_lead_service(service_id):
    obj = LeadService.query.get_or_404(service_id)

    # block delete if used
    in_use = Lead.query.filter(Lead.service_id == obj.id).count()
    if in_use:
        flash("Cannot delete. This service is already used in Leads.", "danger")
        return redirect(url_for("admin_services.lead_services_master"))

    db.session.delete(obj)
    db.session.commit()

    log_audit("LeadService", service_id, "DELETE")
    flash("Service deleted ✅", "success")
    return redirect(url_for("admin_services.lead_services_master"))