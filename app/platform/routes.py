import os
from datetime import datetime
from werkzeug.utils import secure_filename

from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required

from ..platform_models import Tenant
from .. import db
from ..tenant_provision import provision_tenant

platform_bp = Blueprint("platform", __name__, template_folder="../templates", url_prefix="/platform")

ALLOWED_EXTS = {"png", "jpg", "jpeg", "webp"}


def _allowed_file(filename: str) -> bool:
    if not filename or "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in ALLOWED_EXTS


def _save_tenant_logo(file_storage, slug: str) -> str:
    """
    Saves logo into app/static/tenant_logos/<slug>.<ext>
    Returns stored filename (e.g., softech.png)
    """
    if not file_storage or not file_storage.filename:
        return ""

    if not _allowed_file(file_storage.filename):
        raise ValueError("Logo must be png/jpg/jpeg/webp")

    ext = file_storage.filename.rsplit(".", 1)[1].lower()
    filename = secure_filename(f"{slug}.{ext}")

    # store under app/static/tenant_logos
    rel_dir = os.path.join("tenant_logos")
    abs_dir = os.path.join(current_app.root_path, "static", rel_dir)
    os.makedirs(abs_dir, exist_ok=True)

    abs_path = os.path.join(abs_dir, filename)
    file_storage.save(abs_path)
    return filename


def _parse_date(s: str):
    s = (s or "").strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


@platform_bp.route("/tenants")
@login_required
def tenants_list():
    tenants = Tenant.query.order_by(Tenant.created_at.desc()).all()
    return render_template("platform/tenants.html", tenants=tenants)


@platform_bp.route("/tenants/new", methods=["GET", "POST"])
@login_required
def tenants_new():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        slug = request.form.get("slug", "").strip().lower()
        db_uri = request.form.get("db_uri", "").strip()

        admin_email = request.form.get("admin_email", "").strip().lower()
        admin_name = request.form.get("admin_name", "").strip() or "Tenant Admin"
        admin_password = request.form.get("admin_password", "").strip()

        start_date = _parse_date(request.form.get("start_date"))
        end_date = _parse_date(request.form.get("end_date"))

        if not name or not slug or not db_uri:
            flash("Name, Slug, DB URI are required.", "danger")
            return redirect(url_for("platform.tenants_new"))

        if not admin_email or not admin_password:
            flash("Admin Email and Admin Password are required.", "danger")
            return redirect(url_for("platform.tenants_new"))

        if start_date and end_date and end_date < start_date:
            flash("End date cannot be before start date.", "danger")
            return redirect(url_for("platform.tenants_new"))

        if Tenant.query.filter_by(slug=slug).first():
            flash("Slug already exists.", "danger")
            return redirect(url_for("platform.tenants_new"))

        t = Tenant(
            name=name,
            slug=slug,
            db_uri=db_uri,
            is_active=True,
            start_date=start_date,
            end_date=end_date,
        )

        # handle logo upload (optional)
        logo_file = request.files.get("logo")

        try:
            # 1) Save tenant registry first (so we have it)
            db.session.add(t)
            db.session.commit()

            # 2) Save logo if provided
            if logo_file and logo_file.filename:
                stored = _save_tenant_logo(logo_file, slug=t.slug)
                t.logo = stored
                db.session.commit()

            # 3) Provision tenant DB
            provision_tenant(
                db_uri=t.db_uri,
                admin_email=admin_email,
                admin_name=admin_name,
                admin_password=admin_password,
            )

        except Exception as e:
            # rollback registry row if provisioning failed
            try:
                db.session.rollback()
                if t.id:
                    db.session.delete(t)
                    db.session.commit()
            except Exception:
                db.session.rollback()

            flash(f"Tenant provisioning failed: {e}", "danger")
            return redirect(url_for("platform.tenants_new"))

        flash("Tenant created & provisioned.", "success")
        return redirect(url_for("platform.tenants_list"))

    return render_template("platform/tenant_form.html", tenant=None)


@platform_bp.route("/tenants/<int:tenant_id>/edit", methods=["GET", "POST"])
@login_required
def tenants_edit(tenant_id):
    tenant = Tenant.query.get_or_404(tenant_id)

    if request.method == "POST":
        tenant.name = request.form.get("name", "").strip()
        tenant.slug = request.form.get("slug", "").strip().lower()
        tenant.db_uri = request.form.get("db_uri", "").strip()
        tenant.is_active = True if request.form.get("is_active") == "1" else False

        tenant.start_date = _parse_date(request.form.get("start_date"))
        tenant.end_date = _parse_date(request.form.get("end_date"))

        if tenant.start_date and tenant.end_date and tenant.end_date < tenant.start_date:
            flash("End date cannot be before start date.", "danger")
            return redirect(url_for("platform.tenants_edit", tenant_id=tenant.id))

        # logo update (optional)
        logo_file = request.files.get("logo")
        if logo_file and logo_file.filename:
            try:
                tenant.logo = _save_tenant_logo(logo_file, slug=tenant.slug)
            except Exception as e:
                flash(str(e), "danger")
                return redirect(url_for("platform.tenants_edit", tenant_id=tenant.id))

        db.session.commit()
        flash("Tenant updated.", "success")
        return redirect(url_for("platform.tenants_list"))

    return render_template("platform/tenant_form.html", tenant=tenant)


@platform_bp.route("/tenants/<int:tenant_id>/extend", methods=["POST"])
@login_required
def tenants_extend(tenant_id):
    """
    Extends only end_date (keeps start_date as-is)
    """
    tenant = Tenant.query.get_or_404(tenant_id)
    new_end = _parse_date(request.form.get("new_end_date"))

    if not new_end:
        flash("Please select a valid new end date.", "danger")
        return redirect(url_for("platform.tenants_list"))

    if tenant.start_date and new_end < tenant.start_date:
        flash("New end date cannot be before start date.", "danger")
        return redirect(url_for("platform.tenants_list"))

    tenant.end_date = new_end
    db.session.commit()
    flash("Tenant end date extended.", "success")
    return redirect(url_for("platform.tenants_list"))