from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required
from .. import db
from ..utils import require_perm
from ..models import Currency

currencies_bp = Blueprint(
    "currencies",
    __name__,
    url_prefix="/currencies",
    template_folder="../templates"
)

def _clean(s): 
    return (s or "").strip()

@currencies_bp.route("/master", methods=["GET", "POST"])
@login_required
@require_perm("currencies.manage")
def currencies_master():
    if request.method == "POST":
        action = request.form.get("action")

        if action == "create":
            code = _clean(request.form.get("code")).upper()
            name = _clean(request.form.get("name"))
            symbol = _clean(request.form.get("symbol")) or None
            sort_order = int(request.form.get("sort_order") or 1)
            is_active = True if request.form.get("is_active") == "1" else False
            gst_applicable = True if request.form.get("gst_applicable") == "1" else False

            if not code or not name:
                flash("Code and Name are required.", "danger")
                return redirect(url_for("currencies.currencies_master"))

            if Currency.query.filter_by(code=code).first():
                flash("Currency code already exists.", "warning")
                return redirect(url_for("currencies.currencies_master"))

            db.session.add(Currency(
                code=code, name=name, symbol=symbol,
                sort_order=sort_order, is_active=is_active,
                gst_applicable=gst_applicable
            ))
            db.session.commit()
            flash("Currency added ✅", "success")
            return redirect(url_for("currencies.currencies_master"))

        if action == "update":
            cid = int(request.form.get("currency_id"))
            c = Currency.query.get_or_404(cid)

            c.code = _clean(request.form.get("code")).upper() or c.code
            c.name = _clean(request.form.get("name")) or c.name
            c.symbol = _clean(request.form.get("symbol")) or None
            c.sort_order = int(request.form.get("sort_order") or c.sort_order or 1)
            c.is_active = True if request.form.get("is_active") == "1" else False
            c.gst_applicable = True if request.form.get("gst_applicable") == "1" else False

            db.session.commit()
            flash("Currency updated ✅", "success")
            return redirect(url_for("currencies.currencies_master"))

        if action == "delete":
            cid = int(request.form.get("currency_id"))
            c = Currency.query.get_or_404(cid)
            db.session.delete(c)
            db.session.commit()
            flash("Currency deleted ✅", "success")
            return redirect(url_for("currencies.currencies_master"))

    items = Currency.query.order_by(Currency.sort_order.asc(), Currency.code.asc()).all()
    return render_template("admin/currencies_master.html", items=items)