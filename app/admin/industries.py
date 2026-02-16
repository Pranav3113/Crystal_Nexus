from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required

from .. import db
from ..utils import require_perm
from ..models import Industry, Lead

industries_bp = Blueprint("industries", __name__, template_folder="../templates")

def _clean(s): 
    return (s or "").strip()

@industries_bp.route("/industries", methods=["GET", "POST"])
@login_required
@require_perm("industries.manage")
def industries_master():
    if request.method == "POST":
        action = request.form.get("action")

        # ---- Create ----
        if action == "create":
            name = _clean(request.form.get("name"))
            sort_order = int(request.form.get("sort_order") or 0)
            is_active = True if request.form.get("is_active") == "1" else False

            if not name:
                flash("Industry name required.", "danger")
                return redirect(url_for("industries.industries_master"))

            if Industry.query.filter_by(name=name).first():
                flash("Industry already exists.", "danger")
                return redirect(url_for("industries.industries_master"))

            row = Industry(name=name, sort_order=sort_order, is_active=is_active)
            db.session.add(row)
            db.session.commit()

            flash("Industry created ✅", "success")
            return redirect(url_for("industries.industries_master"))

        # ---- Update ----
        if action == "update":
            iid = int(request.form.get("id"))
            row = Industry.query.get_or_404(iid)

            name = _clean(request.form.get("name"))
            sort_order = int(request.form.get("sort_order") or 0)
            is_active = True if request.form.get("is_active") == "1" else False

            if not name:
                flash("Industry name required.", "danger")
                return redirect(url_for("industries.industries_master"))

            # prevent duplicates on update
            dup = Industry.query.filter(Industry.name == name, Industry.id != row.id).first()
            if dup:
                flash("Another industry with this name already exists.", "danger")
                return redirect(url_for("industries.industries_master"))

            row.name = name
            row.sort_order = sort_order
            row.is_active = is_active
            db.session.commit()

            flash("Industry updated ✅", "success")
            return redirect(url_for("industries.industries_master"))

        # ---- Delete (optional; block if used) ----
        if action == "delete":
            iid = int(request.form.get("id"))
            row = Industry.query.get_or_404(iid)

            used = Lead.query.filter(Lead.industry_id == row.id).count()
            if used > 0:
                flash("Industry is used in leads. Please deactivate it instead of deleting.", "warning")
                return redirect(url_for("industries.industries_master"))

            db.session.delete(row)
            db.session.commit()
            flash("Industry deleted ✅", "success")
            return redirect(url_for("industries.industries_master"))

    # list
    items = Industry.query.order_by(Industry.sort_order.asc(), Industry.name.asc()).all()
    return render_template("admin/industries_master.html", items=items)