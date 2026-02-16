from decimal import Decimal
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user

from app import db
from app.utils import require_perm
from app.models import MarginSettings

margin_settings_bp = Blueprint("margin_settings", __name__, template_folder="../templates")


def _clean(s): return (s or "").strip()


@margin_settings_bp.route("/admin/margin-settings", methods=["GET", "POST"])
@login_required
@require_perm("masters.manage")  # or create "margin_settings.manage"
def margin_settings():
    ms = (MarginSettings.query
          .filter(MarginSettings.is_active == True)
          .order_by(MarginSettings.id.desc())
          .first())

    if not ms:
        ms = MarginSettings(threshold_percent=50.00, is_active=True)
        db.session.add(ms)
        db.session.commit()

    if request.method == "POST":
        val = _clean(request.form.get("threshold_percent"))
        try:
            th = Decimal(val)
        except Exception:
            flash("Invalid threshold value.", "danger")
            return redirect(url_for("margin_settings.margin_settings"))

        if th <= 0 or th > 100:
            flash("Threshold must be between 0 and 100.", "danger")
            return redirect(url_for("margin_settings.margin_settings"))

        ms.threshold_percent = th
        ms.updated_by_id = current_user.id
        db.session.commit()

        flash("Margin threshold updated ✅", "success")
        return redirect(url_for("margin_settings.margin_settings"))

    return render_template("admin/margin_settings.html", settings=ms)