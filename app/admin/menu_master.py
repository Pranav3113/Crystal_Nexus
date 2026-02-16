from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required

from .. import db
from ..utils import require_perm
from ..models import Menu, SubMenu, Permission

menu_bp = Blueprint("menu_master", __name__, template_folder="../templates")

def _clean(s):
    return (s or "").strip()

@menu_bp.route("/menu-management", methods=["GET", "POST"])
@login_required
@require_perm("menus.manage")  # or create a dedicated "menus.manage"
def menu_management():
    if request.method == "POST":
        action = request.form.get("action")

        # ---------------- MENUS ----------------
        if action == "menu_create":
            title = _clean(request.form.get("title"))
            icon = _clean(request.form.get("icon"))
            sort_order = int(request.form.get("sort_order") or 1)
            is_active = bool(request.form.get("is_active"))

            if not title:
                flash("Menu title is required.", "danger")
                return redirect(url_for("menu_master.menu_management"))

            db.session.add(Menu(title=title, icon=icon or None, sort_order=sort_order, is_active=is_active))
            db.session.commit()
            flash("Menu created ✅", "success")
            return redirect(url_for("menu_master.menu_management"))

        if action == "menu_update":
            mid = int(request.form.get("menu_id"))
            m = Menu.query.get_or_404(mid)

            m.title = _clean(request.form.get("title")) or m.title
            m.icon = _clean(request.form.get("icon")) or None
            m.sort_order = int(request.form.get("sort_order") or m.sort_order or 1)
            m.is_active = bool(request.form.get("is_active"))
            db.session.commit()

            flash("Menu updated ✅", "success")
            return redirect(url_for("menu_master.menu_management"))

        if action == "menu_delete":
            mid = int(request.form.get("menu_id"))
            m = Menu.query.get_or_404(mid)
            db.session.delete(m)
            db.session.commit()
            flash("Menu deleted ✅", "success")
            return redirect(url_for("menu_master.menu_management"))

        # ---------------- SUBMENUS ----------------
        if action == "submenu_create":
            menu_id = int(request.form.get("menu_id"))
            title = _clean(request.form.get("title"))
            endpoint = _clean(request.form.get("endpoint"))
            url_ = _clean(request.form.get("url"))
            icon = _clean(request.form.get("icon"))
            perm = _clean(request.form.get("permission_code"))
            sort_order = int(request.form.get("sort_order") or 1)
            is_active = bool(request.form.get("is_active"))

            if not title:
                flash("SubMenu title is required.", "danger")
                return redirect(url_for("menu_master.menu_management"))

            if not endpoint and not url_:
                flash("Provide either endpoint or url for SubMenu.", "danger")
                return redirect(url_for("menu_master.menu_management"))

            db.session.add(SubMenu(
                menu_id=menu_id,
                title=title,
                endpoint=endpoint or None,
                url=url_ or None,
                icon=icon or None,
                permission_code=perm or None,
                sort_order=sort_order,
                is_active=is_active
            ))
            db.session.commit()
            flash("SubMenu created ✅", "success")
            return redirect(url_for("menu_master.menu_management"))

        if action == "submenu_update":
            sid = int(request.form.get("submenu_id"))
            s = SubMenu.query.get_or_404(sid)

            s.menu_id = int(request.form.get("menu_id") or s.menu_id)
            s.title = _clean(request.form.get("title")) or s.title
            s.endpoint = _clean(request.form.get("endpoint")) or None
            s.url = _clean(request.form.get("url")) or None
            s.icon = _clean(request.form.get("icon")) or None
            s.permission_code = _clean(request.form.get("permission_code")) or None
            s.sort_order = int(request.form.get("sort_order") or s.sort_order or 1)
            s.is_active = bool(request.form.get("is_active"))

            if not s.endpoint and not s.url:
                flash("Provide either endpoint or url for SubMenu.", "danger")
                return redirect(url_for("menu_master.menu_management"))

            db.session.commit()
            flash("SubMenu updated ✅", "success")
            return redirect(url_for("menu_master.menu_management"))

        if action == "submenu_delete":
            sid = int(request.form.get("submenu_id"))
            s = SubMenu.query.get_or_404(sid)
            db.session.delete(s)
            db.session.commit()
            flash("SubMenu deleted ✅", "success")
            return redirect(url_for("menu_master.menu_management"))

    # GET
    menus = Menu.query.order_by(Menu.sort_order.asc(), Menu.title.asc()).all()
    submenus = SubMenu.query.order_by(SubMenu.menu_id.asc(), SubMenu.sort_order.asc()).all()
    perms = Permission.query.order_by(Permission.code.asc()).all()

    return render_template("admin/menu_management.html", menus=menus, submenus=submenus, perms=perms)