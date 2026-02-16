import os
from datetime import datetime, date
from werkzeug.utils import secure_filename

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, jsonify,
    current_app, send_file, abort
)
from flask_login import login_required, current_user

from .. import db
from ..utils import require_perm
from ..models import (
    Client, ClientBranch, BranchContact, Quote, QuoteStatus, Opportunity,
    ClientDocument,Industry
)

clients_bp = Blueprint("clients", __name__, template_folder="../templates")


def _clean(s):
    return (s or "").strip()


ALLOWED_DOC_EXT = {"pdf", "png", "jpg", "jpeg", "doc", "docx", "xlsx"}
MAX_UPLOAD_MB = 5  # change if needed


@clients_bp.route("/", methods=["GET", "POST"])
@login_required
@require_perm("clients.manage")
def list_clients():
    if request.method == "POST":
        company_name = _clean(request.form.get("company_name"))
        if not company_name:
            flash("Company name is required.", "danger")
            return redirect(url_for("clients.list_clients"))

        industry_id_raw = _clean(request.form.get("industry_id"))  # ✅ from dropdown
        industry_id = int(industry_id_raw) if industry_id_raw else None

        c = Client(
            company_name=company_name,
            industry_id=industry_id,  # ✅ normalized
            service=_clean(request.form.get("service")) or None,
            client_type=_clean(request.form.get("client_type")) or None,
            website=_clean(request.form.get("website")) or None,
            reference=_clean(request.form.get("reference")) or None,
            pan=_clean(request.form.get("pan")) or None,
            client_category=_clean(request.form.get("client_category")) or None,
            remarks=_clean(request.form.get("remarks")) or None,
            is_active=True,
            source="LOCAL",
        )

        # optional: keep legacy text in sync
        if industry_id:
            ind = Industry.query.get(industry_id)
            c.company_industry = ind.name if ind else None

        db.session.add(c)
        db.session.commit()
        flash("Client created ✅", "success")
        return redirect(url_for("clients.view_client", client_id=c.id))

    q = _clean(request.args.get("q"))
    qs = Client.query
    if q:
        like = f"%{q}%"
        qs = qs.filter(Client.company_name.like(like))

    clients = qs.order_by(Client.company_name.asc()).all()

    # ✅ provide industries for dropdown
    industries = Industry.query.filter_by(is_active=True).order_by(Industry.sort_order.asc(), Industry.name.asc()).all()

    return render_template("clients/clients_list.html", clients=clients, q=q, industries=industries)

@clients_bp.route("/<int:client_id>", methods=["GET", "POST"])
@login_required
@require_perm("clients.manage")
def view_client(client_id):
    c = Client.query.get_or_404(client_id)
    industries = Industry.query.filter_by(is_active=True).order_by(Industry.sort_order.asc(), Industry.name.asc()).all()
    # UPDATE CLIENT / ADD BRANCH / UPLOAD DOCUMENT
    if request.method == "POST":
        action = request.form.get("action")

        if action == "update_client":
            c.company_name = _clean(request.form.get("company_name")) or c.company_name
            industry_id_raw = _clean(request.form.get("industry_id"))
            c.industry_id = int(industry_id_raw) if industry_id_raw else None

            # optional sync legacy text
            if c.industry_id:
                ind = Industry.query.get(c.industry_id)
                c.company_industry = ind.name if ind else None
            else:
                c.company_industry = None

            c.service = _clean(request.form.get("service")) or None
            c.client_type = _clean(request.form.get("client_type")) or None
            c.website = _clean(request.form.get("website")) or None
            c.reference = _clean(request.form.get("reference")) or None
            c.pan = _clean(request.form.get("pan")) or None
            c.client_category = _clean(request.form.get("client_category")) or None
            c.remarks = _clean(request.form.get("remarks")) or None
            c.is_active = True if request.form.get("is_active") == "1" else False

            db.session.commit()
            flash("Client updated ✅", "success")
            return redirect(url_for("clients.view_client", client_id=c.id))

        if action == "add_branch":
            branch_location = _clean(request.form.get("branch_location"))
            if not branch_location:
                flash("Branch location is required.", "danger")
                return redirect(url_for("clients.view_client", client_id=c.id))

            b = ClientBranch(
                client_id=c.id,
                branch_location=branch_location,
                address=_clean(request.form.get("address")) or None,
                city=_clean(request.form.get("city")) or None,
                state=_clean(request.form.get("state")) or None,
                country=_clean(request.form.get("country")) or None,
                pin_code=_clean(request.form.get("pin_code")) or None,
                gst=_clean(request.form.get("gst")) or None,
                is_active=True,
            )
            db.session.add(b)
            db.session.flush()

            # ✅ mandatory 1 contact on branch create
            contact_name = _clean(request.form.get("contact_name"))
            if not contact_name:
                db.session.rollback()
                flash("Each branch must have at least one contact. Contact name is required.", "danger")
                return redirect(url_for("clients.view_client", client_id=c.id))

            contact = BranchContact(
                branch_id=b.id,
                name=contact_name,
                phone=_clean(request.form.get("contact_phone")) or None,
                email=_clean(request.form.get("contact_email")) or None,
                designation=_clean(request.form.get("contact_designation")) or None,
                is_primary=True
            )
            db.session.add(contact)
            db.session.commit()

            flash("Branch + contact created ✅", "success")
            return redirect(url_for("clients.view_client", client_id=c.id))

        # ✅ NEW: Upload Client Document (Overall OR Quote)
        if action == "upload_document":
            document_name = _clean(request.form.get("document_name"))
            start_date_raw = _clean(request.form.get("start_date"))
            expiry_date_raw = _clean(request.form.get("expiry_date"))
            quote_id_raw = _clean(request.form.get("quote_id"))  # empty => overall

            f = request.files.get("document_file")

            if not document_name:
                flash("Document name is required.", "danger")
                return redirect(url_for("clients.view_client", client_id=c.id))

            if not start_date_raw or not expiry_date_raw:
                flash("Start date and expiry date are required.", "danger")
                return redirect(url_for("clients.view_client", client_id=c.id))

            try:
                start_date = datetime.strptime(start_date_raw, "%Y-%m-%d").date()
                expiry_date = datetime.strptime(expiry_date_raw, "%Y-%m-%d").date()
            except ValueError:
                flash("Invalid date format.", "danger")
                return redirect(url_for("clients.view_client", client_id=c.id))

            if expiry_date < start_date:
                flash("Expiry date cannot be before start date.", "danger")
                return redirect(url_for("clients.view_client", client_id=c.id))

            quote_id = int(quote_id_raw) if quote_id_raw else None
            if quote_id is not None:
                q = Quote.query.filter_by(id=quote_id, client_id=c.id).first()
                if not q:
                    flash("Invalid quote selected.", "danger")
                    return redirect(url_for("clients.view_client", client_id=c.id))

            if not f or not f.filename:
                flash("Please choose a file to upload.", "danger")
                return redirect(url_for("clients.view_client", client_id=c.id))

            filename = secure_filename(f.filename)
            ext = (filename.rsplit(".", 1)[-1].lower() if "." in filename else "")
            if ext not in ALLOWED_DOC_EXT:
                flash("Invalid file type. Allowed: pdf, images, doc/docx, xlsx.", "danger")
                return redirect(url_for("clients.view_client", client_id=c.id))

            # Optional file size check (works if server provides content_length)
            if request.content_length and request.content_length > MAX_UPLOAD_MB * 1024 * 1024:
                flash(f"File too large. Max allowed is {MAX_UPLOAD_MB} MB.", "danger")
                return redirect(url_for("clients.view_client", client_id=c.id))

            # store path: <app_root>/uploads/client_docs/<client_id>/
            base_dir = os.path.join(current_app.root_path, "uploads", "client_docs", str(c.id))
            os.makedirs(base_dir, exist_ok=True)

            stored_name = f"{int(datetime.utcnow().timestamp())}_{filename}"
            stored_path = os.path.join(base_dir, stored_name)
            f.save(stored_path)

            doc = ClientDocument(
                client_id=c.id,
                quote_id=quote_id,
                document_name=document_name,
                start_date=start_date,
                expiry_date=expiry_date,
                file_name=filename,
                file_path=stored_path,
            )
            # if your model has uploaded_by_id, set it safely
            if hasattr(doc, "uploaded_by_id"):
                doc.uploaded_by_id = getattr(current_user, "employee_id", None) or getattr(current_user, "id", None)

            db.session.add(doc)
            db.session.commit()

            flash("Document uploaded ✅", "success")
            return redirect(url_for("clients.view_client", client_id=c.id))

        flash("Invalid action.", "danger")
        return redirect(url_for("clients.view_client", client_id=c.id))

    # ✅ PREPARE DATA FOR TEMPLATE (NO MODEL CLASS IN JINJA)
    branches = c.branches.order_by(ClientBranch.branch_location.asc()).all()

    contacts_by_branch = {}
    for b in branches:
        contacts_by_branch[b.id] = (
            b.contacts
             .order_by(BranchContact.is_primary.desc(), BranchContact.name.asc())
             .all()
        )

    # ✅ Quotes for document dropdown
    quotes = Quote.query.filter_by(client_id=c.id).order_by(Quote.id.desc()).all()

    # ✅ Documents list
    documents = ClientDocument.query.filter_by(client_id=c.id).order_by(ClientDocument.uploaded_at.desc()).all() \
        if hasattr(ClientDocument, "uploaded_at") else ClientDocument.query.filter_by(client_id=c.id).order_by(ClientDocument.id.desc()).all()

    return render_template(
        "clients/client_detail.html",
        client=c,
        branches=branches,
        contacts_by_branch=contacts_by_branch,
        quotes=quotes,
        documents=documents,
        industries=industries
    )


@clients_bp.route("/documents/<int:doc_id>/download")
@login_required
@require_perm("clients.manage")
def download_client_document(doc_id):
    doc = ClientDocument.query.get_or_404(doc_id)
    if not doc.file_path or not os.path.exists(doc.file_path):
        abort(404)
    return send_file(doc.file_path, as_attachment=True, download_name=doc.file_name or "document")


@clients_bp.route("/documents/<int:doc_id>/delete", methods=["POST"])
@login_required
@require_perm("clients.manage")
def delete_client_document(doc_id):
    doc = ClientDocument.query.get_or_404(doc_id)
    client_id = doc.client_id

    # remove file (best-effort)
    try:
        if doc.file_path and os.path.exists(doc.file_path):
            os.remove(doc.file_path)
    except Exception:
        pass

    db.session.delete(doc)
    db.session.commit()
    flash("Document deleted ✅", "success")
    return redirect(url_for("clients.view_client", client_id=client_id))


@clients_bp.route("/branches/<int:branch_id>/update", methods=["POST"])
@login_required
@require_perm("clients.manage")
def update_branch(branch_id):
    b = ClientBranch.query.get_or_404(branch_id)

    b.branch_location = _clean(request.form.get("branch_location")) or b.branch_location
    b.address = _clean(request.form.get("address")) or None
    b.city = _clean(request.form.get("city")) or None
    b.state = _clean(request.form.get("state")) or None
    b.country = _clean(request.form.get("country")) or None
    b.pin_code = _clean(request.form.get("pin_code")) or None
    b.gst = _clean(request.form.get("gst")) or None
    b.is_active = True if request.form.get("is_active") == "1" else False

    db.session.commit()
    flash("Branch updated ✅", "success")
    return redirect(url_for("clients.view_client", client_id=b.client_id))


@clients_bp.route("/branches/<int:branch_id>/contacts/add", methods=["POST"])
@login_required
@require_perm("clients.manage")
def add_contact(branch_id):
    b = ClientBranch.query.get_or_404(branch_id)

    name = _clean(request.form.get("name"))
    if not name:
        flash("Contact name is required.", "danger")
        return redirect(url_for("clients.view_client", client_id=b.client_id))

    is_primary = True if request.form.get("is_primary") == "1" else False

    if is_primary:
        BranchContact.query.filter_by(branch_id=b.id, is_primary=True).update({"is_primary": False})

    c = BranchContact(
        branch_id=b.id,
        name=name,
        phone=_clean(request.form.get("phone")) or None,
        email=_clean(request.form.get("email")) or None,
        designation=_clean(request.form.get("designation")) or None,
        is_primary=is_primary
    )
    db.session.add(c)
    db.session.commit()

    flash("Contact added ✅", "success")
    return redirect(url_for("clients.view_client", client_id=b.client_id))


@clients_bp.route("/contacts/<int:contact_id>/update", methods=["POST"])
@login_required
@require_perm("clients.manage")
def update_contact(contact_id):
    c = BranchContact.query.get_or_404(contact_id)
    b = c.branch

    c.name = _clean(request.form.get("name")) or c.name
    c.phone = _clean(request.form.get("phone")) or None
    c.email = _clean(request.form.get("email")) or None
    c.designation = _clean(request.form.get("designation")) or None

    make_primary = True if request.form.get("is_primary") == "1" else False
    if make_primary:
        BranchContact.query.filter_by(branch_id=b.id, is_primary=True).update({"is_primary": False})
        c.is_primary = True
    else:
        c.is_primary = c.is_primary

    db.session.commit()
    flash("Contact updated ✅", "success")
    return redirect(url_for("clients.view_client", client_id=b.client_id))


@clients_bp.route("/contacts/<int:contact_id>/delete", methods=["POST"])
@login_required
@require_perm("clients.manage")
def delete_contact(contact_id):
    c = BranchContact.query.get_or_404(contact_id)
    b = c.branch

    if b.contacts.count() <= 1:
        flash("Each branch must have at least one contact. Cannot delete the last contact.", "danger")
        return redirect(url_for("clients.view_client", client_id=b.client_id))

    db.session.delete(c)
    db.session.commit()

    flash("Contact deleted ✅", "success")
    return redirect(url_for("clients.view_client", client_id=b.client_id))


@clients_bp.route("/api/<int:client_id>/branches")
@login_required
def api_branches(client_id):
    branches = ClientBranch.query.filter_by(client_id=client_id, is_active=True)\
        .order_by(ClientBranch.branch_location.asc()).all()
    return jsonify([{"id": b.id, "name": b.branch_location} for b in branches])