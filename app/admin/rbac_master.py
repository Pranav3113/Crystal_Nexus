from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required

from .. import db
from ..utils import require_perm
from ..models import Role, Permission

rbac_bp = Blueprint("rbac", __name__, template_folder="../templates")


def _clean(s): 
    return (s or "").strip()


@rbac_bp.route("/roles", methods=["GET", "POST"])
@login_required
@require_perm("roles.manage")
def roles_master():
    if request.method == "POST":
        action = request.form.get("action")

        # -------- Create Role --------
        if action == "create":
            name = _clean(request.form.get("name"))
            if not name:
                flash("Role name is required.", "danger")
                return redirect(url_for("rbac.roles_master"))

            if Role.query.filter_by(name=name).first():
                flash("Role already exists.", "danger")
                return redirect(url_for("rbac.roles_master"))

            db.session.add(Role(name=name))
            db.session.commit()
            flash("Role created ✅", "success")
            return redirect(url_for("rbac.roles_master"))

        # -------- Update Role --------
        if action == "update":
            rid = int(request.form.get("role_id"))
            r = Role.query.get_or_404(rid)

            name = _clean(request.form.get("name"))
            if not name:
                flash("Role name is required.", "danger")
                return redirect(url_for("rbac.roles_master"))

            if name != r.name and Role.query.filter_by(name=name).first():
                flash("Role name already exists.", "danger")
                return redirect(url_for("rbac.roles_master"))

            r.name = name
            db.session.commit()
            flash("Role updated ✅", "success")
            return redirect(url_for("rbac.roles_master"))

        # -------- Update Role Permissions --------
        if action == "set_permissions":
            rid = int(request.form.get("role_id"))
            r = Role.query.get_or_404(rid)

            perm_ids = request.form.getlist("perm_id")
            ids = [int(x) for x in perm_ids if str(x).isdigit()]

            r.permissions = Permission.query.filter(Permission.id.in_(ids)).all() if ids else []
            db.session.commit()

            flash("Role permissions updated ✅", "success")
            return redirect(url_for("rbac.roles_master"))

    # GET
    q = _clean(request.args.get("q"))
    roles_qs = Role.query
    if q:
        like = f"%{q}%"
        roles_qs = roles_qs.filter(Role.name.like(like))

    roles = roles_qs.order_by(Role.name.asc()).all()
    perms = Permission.query.order_by(Permission.code.asc()).all()

    return render_template("admin/roles_master.html", roles=roles, perms=perms, q=q)


@rbac_bp.route("/permissions", methods=["GET", "POST"])
@login_required
@require_perm("permissions.manage")
def permissions_master():
    if request.method == "POST":
        action = request.form.get("action")

        # -------- Create Permission --------
        if action == "create":
            code = _clean(request.form.get("code"))
            desc = _clean(request.form.get("description"))

            if not code:
                flash("Permission code is required.", "danger")
                return redirect(url_for("rbac.permissions_master"))

            if Permission.query.filter_by(code=code).first():
                flash("Permission already exists.", "danger")
                return redirect(url_for("rbac.permissions_master"))

            db.session.add(Permission(code=code, description=desc or None))
            db.session.commit()
            flash("Permission created ✅", "success")
            return redirect(url_for("rbac.permissions_master"))

        # -------- Update Permission --------
        if action == "update":
            pid = int(request.form.get("permission_id"))
            p = Permission.query.get_or_404(pid)

            # We typically lock 'code' (used everywhere), allow description edits
            p.description = _clean(request.form.get("description")) or None
            db.session.commit()

            flash("Permission updated ✅", "success")
            return redirect(url_for("rbac.permissions_master"))

    q = _clean(request.args.get("q"))
    qs = Permission.query
    if q:
        like = f"%{q}%"
        qs = qs.filter((Permission.code.like(like)) | (Permission.description.like(like)))

    perms = qs.order_by(Permission.code.asc()).all()
    return render_template("admin/permissions_master.html", perms=perms, q=q)