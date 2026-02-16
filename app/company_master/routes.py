# app/company_master/routes.py
import os
from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required
from werkzeug.utils import secure_filename

from app import db
from app.utils import require_perm
from app.models import Company, CompanyBranch

company_bp = Blueprint("company_master", __name__, url_prefix="/company", template_folder="../templates")

ALLOWED_LOGO_EXTS = {"png", "jpg", "jpeg", "webp"}
MAX_LOGO_MB = 2


def _allowed_file(filename: str) -> bool:
    if not filename or "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[-1].lower()
    return ext in ALLOWED_LOGO_EXTS


def _try_delete_old_logo(rel_path: str):
    """Best-effort cleanup of old logo file stored under static/."""
    try:
        if not rel_path:
            return
        abs_path = os.path.join(current_app.static_folder, rel_path)
        if os.path.exists(abs_path):
            os.remove(abs_path)
    except Exception:
        pass


def _save_logo(file_storage):
    if not file_storage or not file_storage.filename:
        return None

    filename = secure_filename(file_storage.filename)
    if not _allowed_file(filename):
        raise ValueError("Logo must be png/jpg/jpeg/webp")

    # size check
    file_storage.stream.seek(0, os.SEEK_END)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if size > MAX_LOGO_MB * 1024 * 1024:
        raise ValueError(f"Logo must be <= {MAX_LOGO_MB} MB")

    folder = os.path.join(current_app.static_folder, "uploads", "company_logos")
    os.makedirs(folder, exist_ok=True)

    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    new_name = f"{ts}_{filename}"
    abs_path = os.path.join(folder, new_name)

    file_storage.save(abs_path)

    # store relative path so it works across environments
    rel_path = f"uploads/company_logos/{new_name}"
    return rel_path


@company_bp.route("/master", methods=["GET", "POST"])
@login_required
@require_perm("company.manage")
def company_master():
    action = request.form.get("action") if request.method == "POST" else None

    if request.method == "POST":
        try:
            # -------------------------
            # Company create/update
            # -------------------------
            if action in ("company_create", "company_update"):
                cid = request.form.get("company_id")
                name = (request.form.get("name") or "").strip()
                pan = (request.form.get("pan") or "").strip() or None
                is_active = True if request.form.get("is_active") == "1" else False

                if not name:
                    flash("Company name is required.", "danger")
                    return redirect(url_for("company_master.company_master"))

                if action == "company_create":
                    if Company.query.filter(db.func.lower(Company.name) == name.lower()).first():
                        flash("Company with this name already exists.", "danger")
                        return redirect(url_for("company_master.company_master"))

                    c = Company(name=name, pan=pan, is_active=is_active, created_at=datetime.utcnow())

                    logo_file = request.files.get("logo")
                    if logo_file and logo_file.filename:
                        c.logo_path = _save_logo(logo_file)

                    db.session.add(c)
                    db.session.commit()
                    flash("Company created ✅", "success")
                    return redirect(url_for("company_master.company_master"))

                # update
                c = Company.query.get_or_404(int(cid))
                c.name = name
                c.pan = pan
                c.is_active = is_active

                logo_file = request.files.get("logo")
                if logo_file and logo_file.filename:
                    old_logo = c.logo_path
                    c.logo_path = _save_logo(logo_file)
                    db.session.commit()
                    _try_delete_old_logo(old_logo)
                    flash("Company updated ✅", "success")
                    return redirect(url_for("company_master.company_master"))

                db.session.commit()
                flash("Company updated ✅", "success")
                return redirect(url_for("company_master.company_master"))

            # -------------------------
            # Company toggle
            # -------------------------
            if action == "company_toggle":
                cid = int(request.form.get("company_id"))
                c = Company.query.get_or_404(cid)
                c.is_active = not bool(c.is_active)
                db.session.commit()
                flash("Company status updated ✅", "success")
                return redirect(url_for("company_master.company_master", open_company=cid))

            # -------------------------
            # Branch add/update
            # -------------------------
            if action in ("branch_add", "branch_update"):
                cid = int(request.form.get("company_id"))
                branch_id = request.form.get("branch_id")

                branch_name = (request.form.get("branch_name") or "").strip()
                branch_address = (request.form.get("branch_address") or "").strip() or None
                state = (request.form.get("state") or "").strip() or None  # ✅ IMPORTANT for GST logic
                gst_no = (request.form.get("gst_no") or "").strip() or None
                is_active = True if request.form.get("branch_is_active") == "1" else False

                if not branch_name:
                    flash("Branch name is required.", "danger")
                    return redirect(url_for("company_master.company_master", open_company=cid))

                # unique per company
                q = (CompanyBranch.query.filter_by(company_id=cid)
                     .filter(db.func.lower(CompanyBranch.branch_name) == branch_name.lower()))
                if action == "branch_update":
                    q = q.filter(CompanyBranch.id != int(branch_id))
                if q.first():
                    flash("Branch name already exists for this company.", "danger")
                    return redirect(url_for("company_master.company_master", open_company=cid))

                if action == "branch_add":
                    b = CompanyBranch(
                        company_id=cid,
                        branch_name=branch_name,
                        branch_address=branch_address,
                        state=state,
                        gst_no=gst_no,
                        is_active=is_active,
                        created_at=datetime.utcnow()
                    )
                    db.session.add(b)
                    db.session.commit()
                    flash("Branch added ✅", "success")
                    return redirect(url_for("company_master.company_master", open_company=cid))

                b = CompanyBranch.query.get_or_404(int(branch_id))
                b.branch_name = branch_name
                b.branch_address = branch_address
                b.state = state
                b.gst_no = gst_no
                b.is_active = is_active
                db.session.commit()
                flash("Branch updated ✅", "success")
                return redirect(url_for("company_master.company_master", open_company=cid))

            # -------------------------
            # Branch toggle
            # -------------------------
            if action == "branch_toggle":
                bid = int(request.form.get("branch_id"))
                b = CompanyBranch.query.get_or_404(bid)
                b.is_active = not bool(b.is_active)
                db.session.commit()
                flash("Branch status updated ✅", "success")
                return redirect(url_for("company_master.company_master", open_company=b.company_id))

        except ValueError as e:
            db.session.rollback()
            flash(str(e), "danger")
            return redirect(url_for("company_master.company_master"))
        except Exception:
            db.session.rollback()
            flash("Action failed.", "danger")
            return redirect(url_for("company_master.company_master"))

    # -------------------------
    # GET list
    # -------------------------
    q = (request.args.get("q") or "").strip()
    show_inactive = (request.args.get("show_inactive") or "").strip() in ("1", "true", "yes", "on")
    open_company = request.args.get("open_company", type=int)

    qs = Company.query
    if q:
        like = f"%{q}%"
        qs = qs.filter(
            Company.name.ilike(like) |
            Company.pan.ilike(like)
        )

    if not show_inactive:
        qs = qs.filter(Company.is_active.is_(True))

    qs = qs.order_by(Company.created_at.desc(), Company.id.desc())

    page = request.args.get("page", 1, type=int)
    pagination = qs.paginate(page=page, per_page=10, error_out=False)

    company_ids = [c.id for c in pagination.items]
    branches = []
    if company_ids:
        branches = (CompanyBranch.query
                    .filter(CompanyBranch.company_id.in_(company_ids))
                    .order_by(CompanyBranch.company_id.asc(), CompanyBranch.branch_name.asc())
                    .all())

    branches_by_company = {}
    for b in branches:
        branches_by_company.setdefault(b.company_id, []).append(b)

    return render_template(
        "company/company_master.html",
        pagination=pagination,
        q=q,
        show_inactive=show_inactive,
        branches_by_company=branches_by_company,
        open_company=open_company
    )