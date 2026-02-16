from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required

from .. import db
from ..utils import require_perm
from ..models import Designation

designations_bp = Blueprint("designations", __name__, template_folder="../templates")

def _clean(s): return (s or "").strip()

@designations_bp.route("/designations", methods=["GET", "POST"])
@login_required
@require_perm("designations.manage")
def designation_master():
    if request.method == "POST":
        action = request.form.get("action")

        if action == "create":
            name = _clean(request.form.get("name"))
            code = _clean(request.form.get("code"))
            if not name:
                flash("Designation name required.", "danger")
                return redirect(url_for("designations.designation_master"))

            if Designation.query.filter_by(name=name).first():
                flash("Designation already exists.", "danger")
                return redirect(url_for("designations.designation_master"))

            d = Designation(name=name, code=code or None, source="LOCAL", is_active=True)
            db.session.add(d)
            db.session.commit()
            flash("Designation created ✅", "success")
            return redirect(url_for("designations.designation_master"))

        if action == "update":
            did = int(request.form.get("id"))
            d = Designation.query.get_or_404(did)

            # lock external names if you want:
            if d.source != "LOCAL":
                flash("External designation is HRMS-managed. You can only activate/deactivate.", "warning")
            else:
                d.name = _clean(request.form.get("name")) or d.name
                d.code = _clean(request.form.get("code")) or None

            d.is_active = True if request.form.get("is_active") == "1" else False
            db.session.commit()
            flash("Designation updated ✅", "success")
            return redirect(url_for("designations.designation_master"))

    items = Designation.query.order_by(Designation.name.asc()).all()
    return render_template("admin/designations_master.html", items=items)