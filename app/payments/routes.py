from decimal import Decimal
from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from sqlalchemy import func

from app import db
from app.utils import require_perm
from app.models import Invoice, InvoicePayment

payments_bp = Blueprint("payments", __name__, url_prefix="/payments")


def get_current_collected(invoice_id: int, exclude_payment_id: int = None):
    q = (db.session.query(func.coalesce(func.sum(InvoicePayment.amount), 0))
         .filter(InvoicePayment.invoice_id == invoice_id)
         .filter(InvoicePayment.status != "Rejected"))
    if exclude_payment_id:
        q = q.filter(InvoicePayment.id != exclude_payment_id)
    return q.scalar()


def validate_collection(invoice: Invoice, new_amount, exclude_payment_id: int = None):
    current = Decimal(str(get_current_collected(invoice.id, exclude_payment_id=exclude_payment_id)))
    new_amount = Decimal(str(new_amount))

    if new_amount <= 0:
        raise ValueError("Amount must be greater than 0.")

    if current + new_amount > Decimal(str(invoice.total_amount)):
        remaining = Decimal(str(invoice.total_amount)) - current
        raise ValueError(f"Collection exceeds invoice amount. Remaining: {remaining}")


def ensure_owner_or_admin(invoice: Invoice):
    # invoice -> quote -> opportunity -> owner
    opp_owner_id = invoice.quote.opportunity.owner_id if (invoice.quote and invoice.quote.opportunity) else None
    if opp_owner_id != current_user.id and not current_user.has_perm("payments.admin"):
        abort(403)


def ensure_finance_or_admin():
    if not (current_user.has_perm("payments.verify") or current_user.has_perm("payments.admin")):
        abort(403)


# =========================================================
# ✅ Quote → Payments (redirect wrapper)
# =========================================================
@payments_bp.route("/quote/<int:quote_id>/payments", methods=["GET"])
@login_required
@require_perm("payments.view")
def quote_payments(quote_id):
    # Find latest non-cancelled invoice for this quote
    inv = (Invoice.query
           .filter_by(quote_id=quote_id)
           .filter(Invoice.status != "Cancelled")
           .order_by(Invoice.id.desc())
           .first())

    if not inv:
        flash("No invoice found for this quote. Create an invoice first.", "warning")
        return redirect(url_for("quotes.view_quote", quote_id=quote_id))

    return redirect(url_for("payments.invoice_payments", invoice_id=inv.id))


# =========================================================
# Invoice Payments page
# =========================================================
@payments_bp.route("/invoice/<int:invoice_id>", methods=["GET", "POST"])
@login_required
@require_perm("payments.view")
def invoice_payments(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)
    ensure_owner_or_admin(invoice)

    if request.method == "POST":
        if not current_user.has_perm("payments.add") and not current_user.has_perm("payments.admin"):
            abort(403)

        try:
            payment_date = datetime.strptime(request.form["payment_date"], "%Y-%m-%d").date()
            amount = (request.form.get("amount") or "").replace(",", "").strip()
            transfer_type = (request.form.get("transfer_type") or "").strip()
            reference = request.form.get("reference", "").strip() or None

            if not transfer_type:
                raise ValueError("Transfer type is required.")

            validate_collection(invoice, amount)

            p = InvoicePayment(
                invoice_id=invoice.id,
                payment_date=payment_date,
                amount=amount,
                transfer_type=transfer_type,
                reference=reference,
                status="Pending",
                created_by_id=current_user.id
            )
            db.session.add(p)
            db.session.commit()
            flash("Payment added and sent for finance verification.", "success")

        except ValueError as e:
            db.session.rollback()
            flash(str(e), "danger")
        except Exception:
            db.session.rollback()
            flash("Failed to add payment.", "danger")

        return redirect(url_for("payments.invoice_payments", invoice_id=invoice.id))

    payments = (InvoicePayment.query
                .filter_by(invoice_id=invoice.id)
                .order_by(InvoicePayment.payment_date.desc(), InvoicePayment.created_at.desc())
                .all())

    collected = invoice.collected_amount()
    remaining = invoice.remaining_amount()

    return render_template(
        "payments/invoice_payments.html",
        invoice=invoice,
        payments=payments,
        collected=collected,
        remaining=remaining
    )


# =========================================================
# Finance Queue
# =========================================================
@payments_bp.route("/finance/queue", methods=["GET"])
@login_required
@require_perm("payments.verify")
def finance_payment_queue():
    ensure_finance_or_admin()
    pending = (InvoicePayment.query
               .filter_by(status="Pending")
               .order_by(InvoicePayment.created_at.asc())
               .all())
    return render_template("payments/finance_payment_queue.html", pending=pending)


@payments_bp.route("/finance/payment/<int:payment_id>/action", methods=["POST"])
@login_required
@require_perm("payments.verify")
def finance_payment_action(payment_id):
    ensure_finance_or_admin()

    p = InvoicePayment.query.get_or_404(payment_id)
    action = request.form.get("action")  # verify / reject
    remarks = request.form.get("finance_remarks", "").strip() or None

    try:
        if action == "verify":
            invoice = Invoice.query.get_or_404(p.invoice_id)
            validate_collection(invoice, p.amount, exclude_payment_id=p.id)

            p.status = "Verified"
            p.verified_by_id = current_user.id
            p.verified_at = datetime.utcnow()
            p.finance_remarks = remarks

            # update invoice status
            if invoice.remaining_amount() <= 0:
                invoice.status = "Paid"
            else:
                invoice.status = "Partially Paid"

        elif action == "reject":
            p.status = "Rejected"
            p.verified_by_id = current_user.id
            p.verified_at = datetime.utcnow()
            p.finance_remarks = remarks

        else:
            flash("Invalid action.", "danger")
            return redirect(url_for("payments.finance_payment_queue"))

        db.session.commit()
        flash(f"Payment {p.status.lower()} successfully.", "success")

    except ValueError as e:
        db.session.rollback()
        flash(str(e), "danger")
    except Exception:
        db.session.rollback()
        flash("Action failed.", "danger")

    return redirect(url_for("payments.finance_payment_queue"))