from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required
from werkzeug.security import generate_password_hash
from decimal import Decimal
from .. import db
from ..utils import require_perm
from ..models import (
    User, Role, EmployeeProfile, Designation,
    QuoteApproval, ApprovalRuleStep,
    CompanyBranch, Company  # ✅ add
)

user_master_bp = Blueprint("user_master", __name__, template_folder="../templates")


def _clean(s):
    return (s or "").strip()


@user_master_bp.route("/users", methods=["GET", "POST"])
@login_required
@require_perm("users.manage")
def users_master():
    # ---------- CREATE ----------
    if request.method == "POST":
        action = request.form.get("action")

        if action == "create":
            name = _clean(request.form.get("name"))
            email = _clean(request.form.get("email")).lower()
            role_id = request.form.get("role_id")
            auth_provider = _clean(request.form.get("auth_provider")) or "LOCAL"
            password = request.form.get("password") or ""

            employee_code = _clean(request.form.get("employee_code"))
            designation_id = request.form.get("designation_id")
            department = _clean(request.form.get("department"))
            manager_id = request.form.get("reporting_manager_user_id")
            monthly_ctc = request.form.get("monthly_ctc") or "0"
            team_role = _clean(request.form.get("team_role")) or None
            prof.team_role = team_role

            # ✅ NEW: Company Branch
            cb = _clean(request.form.get("company_branch_id"))
            company_branch_id = int(cb) if cb.isdigit() else None

            if not name or not email:
                flash("Name and Email are required.", "danger")
                return redirect(url_for("user_master.users_master"))

            if User.query.filter_by(email=email).first():
                flash("Email already exists.", "danger")
                return redirect(url_for("user_master.users_master"))

            u = User(
                name=name,
                email=email,
                role_id=int(role_id) if (role_id and role_id.isdigit()) else None,
                auth_provider=auth_provider,
                is_active=True,
                company_branch_id=company_branch_id,  # ✅ save
                monthly_ctc=Decimal(str(monthly_ctc).strip() or "0"),
            )

            if auth_provider == "LOCAL":
                if not password:
                    flash("Password is required for LOCAL users.", "danger")
                    return redirect(url_for("user_master.users_master"))
                u.password_hash = generate_password_hash(password)
            else:
                u.password_hash = None

            db.session.add(u)
            db.session.flush()

            prof = EmployeeProfile(
                user_id=u.id,
                employee_code=employee_code or None,
                designation_id=int(designation_id) if (designation_id and designation_id.isdigit()) else None,
                department=department or None,
                reporting_manager_user_id=int(manager_id) if (manager_id and manager_id.isdigit()) else None,
                team_role=team_role,
                source="LOCAL" if auth_provider == "LOCAL" else "HRMS"
            )
            db.session.add(prof)
            db.session.commit()

            flash("User created ✅", "success")
            return redirect(url_for("user_master.users_master"))

    # ---------- LIST / FILTER ----------
    q = _clean(request.args.get("q"))
    provider = _clean(request.args.get("provider"))
    role = _clean(request.args.get("role"))

    qs = User.query

    if q:
        like = f"%{q}%"
        qs = qs.filter((User.name.like(like)) | (User.email.like(like)))

    if provider:
        qs = qs.filter(User.auth_provider == provider)

    if role.isdigit():
        qs = qs.filter(User.role_id == int(role))

    users = qs.order_by(User.id.desc()).all()

    roles = Role.query.order_by(Role.name.asc()).all()
    designations = Designation.query.filter_by(is_active=True).order_by(Designation.name.asc()).all()
    managers = User.query.filter_by(is_active=True).order_by(User.name.asc()).all()

    # ✅ NEW: branch dropdown data (show company + branch name)
    branches = (CompanyBranch.query
                .join(Company)
                .filter(CompanyBranch.is_active == True)
                .filter(Company.is_active == True)
                .order_by(Company.name.asc(), CompanyBranch.branch_name.asc())
                .all())

    return render_template(
        "admin/users_master.html",
        users=users,
        roles=roles,
        designations=designations,
        managers=managers,
        branches=branches,  # ✅ pass to template
        q=q,
        provider=provider,
        role_filter=role
    )


from decimal import Decimal

@user_master_bp.route("/users/<int:user_id>/update", methods=["POST"])
@login_required
@require_perm("users.manage")
def update_user(user_id):
    u = User.query.get_or_404(user_id)

    can_edit_identity = (u.auth_provider == "LOCAL")

    name = _clean(request.form.get("name"))
    email = _clean(request.form.get("email")).lower()
    role_id = request.form.get("role_id")
    is_active = True if request.form.get("is_active") == "1" else False

    # ✅ Company Branch update
    cb = _clean(request.form.get("company_branch_id"))
    u.company_branch_id = int(cb) if cb.isdigit() else None

    # ✅ Monthly CTC update (always editable by Admin/HR)
    monthly_ctc = _clean(request.form.get("monthly_ctc")) or "0"
    try:
        u.monthly_ctc = Decimal(monthly_ctc)
    except Exception:
        u.monthly_ctc = Decimal("0")

    if can_edit_identity:
        if not name or not email:
            flash("Name and Email are required.", "danger")
            return redirect(url_for("user_master.users_master"))

        if email != u.email and User.query.filter_by(email=email).first():
            flash("Email already exists.", "danger")
            return redirect(url_for("user_master.users_master"))

        u.name = name
        u.email = email

    u.role_id = int(role_id) if (role_id and role_id.isdigit()) else None

    # ---- deactivation guard ----
    if u.is_active and (is_active is False):
        pending_count = (QuoteApproval.query
            .filter(QuoteApproval.approver_user_id == u.id)
            .filter(QuoteApproval.status.in_(["PENDING", "WAITING"]))
            .count()
        )
        step_count = (ApprovalRuleStep.query
            .filter_by(is_active=True, approver_user_id=u.id)
            .count()
        )
        if pending_count > 0 or step_count > 0:
            flash(
                f"Cannot deactivate user. "
                f"Active approvals assigned: {pending_count}, "
                f"active approval-rule steps: {step_count}. "
                f"Reassign or disable steps first.",
                "danger"
            )
            return redirect(url_for("user_master.users_master"))

    u.is_active = is_active

    # ✅ Ensure prof exists BEFORE referencing it
    prof = u.profile
    if not prof:
        prof = EmployeeProfile(user_id=u.id)

    prof.employee_code = _clean(request.form.get("employee_code")) or None

    desig = request.form.get("designation_id")
    prof.designation_id = int(desig) if (desig and desig.isdigit()) else None

    prof.department = _clean(request.form.get("department")) or None

    mgr = request.form.get("reporting_manager_user_id")
    prof.reporting_manager_user_id = int(mgr) if (mgr and mgr.isdigit()) else None

    # ✅ Team role (BD/AM)
    prof.team_role = _clean(request.form.get("team_role")) or None

    db.session.add(prof)
    db.session.commit()

    flash("User updated ✅", "success")
    return redirect(url_for("user_master.users_master"))

@user_master_bp.route("/users/<int:user_id>/reset-password", methods=["POST"])
@login_required
@require_perm("users.manage")
def reset_password(user_id):
    u = User.query.get_or_404(user_id)

    if u.auth_provider != "LOCAL":
        flash("Password is not managed for HRMS/SSO users.", "warning")
        return redirect(url_for("user_master.users_master"))

    new_password = request.form.get("new_password") or ""
    if not new_password:
        flash("New password is required.", "danger")
        return redirect(url_for("user_master.users_master"))

    u.password_hash = generate_password_hash(new_password)
    db.session.commit()

    flash("Password reset ✅", "success")
    return redirect(url_for("user_master.users_master"))