from datetime import date, datetime
from decimal import Decimal
from flask_login import UserMixin
from sqlalchemy import func
from werkzeug.security import generate_password_hash, check_password_hash

from . import db, login_manager

# -------------------------
# RBAC
# -------------------------
role_permissions = db.Table(
    "role_permissions",
    db.Column("role_id", db.Integer, db.ForeignKey("roles.id"), primary_key=True),
    db.Column("permission_id", db.Integer, db.ForeignKey("permissions.id"), primary_key=True),
)


class Role(db.Model):
    __tablename__ = "roles"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)

    permissions = db.relationship("Permission", secondary=role_permissions, backref="roles")


class Permission(db.Model):
    __tablename__ = "permissions"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(120), unique=True, nullable=False)  # e.g. leads.view
    description = db.Column(db.String(255))


class Menu(db.Model):
    __tablename__ = "menus"
    id = db.Column(db.Integer, primary_key=True)

    title = db.Column(db.String(120), nullable=False)
    icon = db.Column(db.String(64))  # bootstrap-icons name e.g. "speedometer2"
    sort_order = db.Column(db.Integer, default=1)
    is_active = db.Column(db.Boolean, default=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    submenus = db.relationship(
        "SubMenu",
        backref="menu",
        lazy="dynamic",
        cascade="all, delete-orphan",
        order_by="SubMenu.sort_order.asc()",
    )

class SubMenu(db.Model):
    __tablename__ = "submenus"
    id = db.Column(db.Integer, primary_key=True)

    menu_id = db.Column(db.Integer, db.ForeignKey("menus.id"), nullable=False)

    title = db.Column(db.String(120), nullable=False)
    endpoint = db.Column(db.String(160))  # e.g. "leads.list_leads"
    url = db.Column(db.String(255))       # optional hard URL: "/leads"
    icon = db.Column(db.String(64))       # optional submenu icon
    sort_order = db.Column(db.Integer, default=1)
    is_active = db.Column(db.Boolean, default=True)

    # optional RBAC hook
    permission_code = db.Column(db.String(120))  # e.g. "leads.view" (can be NULL)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# -------------------------
# User + HR Profile (supports LOCAL + HRMS/SSO)
# -------------------------
class User(db.Model, UserMixin):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)

    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)

    # HRMS/SSO users won't have local password
    password_hash = db.Column(db.String(255), nullable=True)

    is_active = db.Column(db.Boolean, default=True)

    # LOCAL | HRMS | SSO
    auth_provider = db.Column(db.String(20), nullable=False, default="LOCAL")

    # external identity (optional)
    external_user_id = db.Column(db.String(120), nullable=True, index=True)
    last_synced_at = db.Column(db.DateTime, nullable=True)

    role_id = db.Column(db.Integer, db.ForeignKey("roles.id"))
    role = db.relationship("Role")

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    company_branch_id = db.Column(db.Integer, db.ForeignKey("company_branches.id"), nullable=True, index=True)
    company_branch = db.relationship("CompanyBranch", foreign_keys=[company_branch_id])
    
    monthly_ctc = db.Column(db.Numeric(12, 2), nullable=True, default=0, index=True)

    # 1–1 profile
    profile = db.relationship(
        "EmployeeProfile",
        uselist=False,
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="EmployeeProfile.user_id",
    )

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(
            password,
            method="pbkdf2:sha256",
            salt_length=16
        )

    def check_password(self, password: str) -> bool:
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

    def has_perm(self, code: str) -> bool:
        if not self.role:
            return False
        return any(p.code == code for p in self.role.permissions)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


class Designation(db.Model):
    __tablename__ = "designations"
    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(db.String(120), unique=True, nullable=False)
    code = db.Column(db.String(60), nullable=True)

    # LOCAL | HRMS
    source = db.Column(db.String(20), nullable=False, default="LOCAL")
    external_designation_id = db.Column(db.String(120), nullable=True, index=True)
    last_synced_at = db.Column(db.DateTime, nullable=True)

    is_active = db.Column(db.Boolean, default=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class EmployeeProfile(db.Model):
    __tablename__ = "employee_profiles"
    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False)
    user = db.relationship("User", back_populates="profile", foreign_keys=[user_id])

    employee_code = db.Column(db.String(50), unique=True, nullable=True, index=True)

    designation_id = db.Column(db.Integer, db.ForeignKey("designations.id"), nullable=True)
    designation = db.relationship("Designation", backref="employee_profiles")
    team_role = db.Column(db.String(10), nullable=True)  # BD / AM
    

    department = db.Column(db.String(120), nullable=True)

    reporting_manager_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    reporting_manager = db.relationship(
        "User",
        foreign_keys=[reporting_manager_user_id],
        backref=db.backref("direct_reports", lazy="dynamic")
    )

    # LOCAL | HRMS
    source = db.Column(db.String(20), nullable=False, default="LOCAL")

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# -------------------------
# Clients (Customer Master)
# -------------------------
class Client(db.Model):
    __tablename__ = "clients"
    id = db.Column(db.Integer, primary_key=True)

    company_name = db.Column(db.String(200), nullable=False, index=True)
    industry_id = db.Column(db.Integer, db.ForeignKey("industries.id"), nullable=True, index=True)
    industry = db.relationship("Industry", foreign_keys=[industry_id])
    company_industry = db.Column(db.String(120), nullable=True)
    service = db.Column(db.String(200), nullable=True)

    # Domestic | International
    client_type = db.Column(db.String(30), nullable=True)

    website = db.Column(db.String(200), nullable=True)
    reference = db.Column(db.String(200), nullable=True)

    pan = db.Column(db.String(20), nullable=True, index=True)

    # Individual | Corporate | LSP
    client_category = db.Column(db.String(30), nullable=True)

    remarks = db.Column(db.Text, nullable=True)

    is_active = db.Column(db.Boolean, default=True)

    # LOCAL | HRMS (future-ready)
    source = db.Column(db.String(20), nullable=False, default="LOCAL")
    external_client_id = db.Column(db.String(120), nullable=True, index=True)
    last_synced_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    branches = db.relationship(
        "ClientBranch",
        back_populates="client",
        cascade="all, delete-orphan",
        lazy="dynamic"
    )


class ClientBranch(db.Model):
    __tablename__ = "client_branches"
    id = db.Column(db.Integer, primary_key=True)

    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    client = db.relationship("Client", back_populates="branches", foreign_keys=[client_id])

    # “Branch location” label/name
    branch_location = db.Column(db.String(150), nullable=False)

    address = db.Column(db.Text, nullable=True)
    city = db.Column(db.String(100), nullable=True)
    state = db.Column(db.String(100), nullable=True)
    country = db.Column(db.String(100), nullable=True)
    pin_code = db.Column(db.String(20), nullable=True)

    gst = db.Column(db.String(30), nullable=True, index=True)

    is_active = db.Column(db.Boolean, default=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    contacts = db.relationship(
        "BranchContact",
        back_populates="branch",
        cascade="all, delete-orphan",
        lazy="dynamic"
    )

    __table_args__ = (
        # Unique branch label per client
        db.UniqueConstraint("client_id", "branch_location", name="uq_client_branch_location"),
    )


class BranchContact(db.Model):
    """
    Each branch must have at least one contact person.
    NOTE: This “at least one” rule is enforced in routes/forms (application-level validation),
    since DB constraints can’t guarantee “minimum 1 child row” reliably.
    """
    __tablename__ = "branch_contacts"
    id = db.Column(db.Integer, primary_key=True)

    branch_id = db.Column(db.Integer, db.ForeignKey("client_branches.id"), nullable=False, index=True)
    branch = db.relationship("ClientBranch", back_populates="contacts", foreign_keys=[branch_id])

    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(30), nullable=True)
    email = db.Column(db.String(120), nullable=True, index=True)

    # store designation as free text (client-side designation, not your internal Designation master)
    designation = db.Column(db.String(120), nullable=True)

    is_primary = db.Column(db.Boolean, default=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# -------------------------
# Leads
# -------------------------

class Currency(db.Model):
    __tablename__ = "currencies"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(10), unique=True, nullable=False)   # INR, USD
    name = db.Column(db.String(80), nullable=False)               # Indian Rupee
    symbol = db.Column(db.String(10))                             # ₹, $
    is_active = db.Column(db.Boolean, default=True)
    gst_applicable = db.Column(db.Boolean, default=False)          # ✅ only INR should be True by default
    sort_order = db.Column(db.Integer, default=1)
    
class LeadStatus(db.Model):
    __tablename__ = "lead_statuses"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(60), unique=True, nullable=False)
    color = db.Column(db.String(30), default="secondary")
    is_active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)


class LeadSource(db.Model):
    __tablename__ = "lead_sources"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)

class LeadService(db.Model):
    __tablename__ = "lead_services"
    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(db.String(120), unique=True, nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Lead(db.Model):
    __tablename__ = "leads"
    id = db.Column(db.Integer, primary_key=True)

    lead_code = db.Column(db.String(30), unique=True, index=True)
    name = db.Column(db.String(120), nullable=False)
    company = db.Column(db.String(120))
    email = db.Column(db.String(120), index=True)
    
    phone_country = db.Column(db.String(6), default="+91")   # store +91, +1 etc
    phone = db.Column(db.String(30), index=True)             # keep existing phone number
    location = db.Column(db.String(120))

    industry_id = db.Column(db.Integer, db.ForeignKey("industries.id"), nullable=True, index=True)
    industry = db.relationship("Industry")
    website = db.Column(db.String(200))

    # ✅ NEW: if lead is from existing customer
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=True, index=True)
    client = db.relationship("Client", foreign_keys=[client_id])

    branch_id = db.Column(db.Integer, db.ForeignKey("client_branches.id"), nullable=True, index=True)
    branch = db.relationship("ClientBranch", foreign_keys=[branch_id])

    status_id = db.Column(db.Integer, db.ForeignKey("lead_statuses.id"))
    status = db.relationship("LeadStatus")

    source_id = db.Column(db.Integer, db.ForeignKey("lead_sources.id"))
    source = db.relationship("LeadSource")

    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    owner = db.relationship("User")

    notes = db.Column(db.Text)

    estimated_closure_date = db.Column(db.Date, nullable=True)

    service_id = db.Column(db.Integer, db.ForeignKey("lead_services.id"), nullable=True, index=True)
    service = db.relationship("LeadService", foreign_keys=[service_id])

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ActivityType(db.Model):
    __tablename__ = "activity_types"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    icon = db.Column(db.String(40), default="telephone")
    is_active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)


class LeadActivity(db.Model):
    __tablename__ = "lead_activities"
    id = db.Column(db.Integer, primary_key=True)

    lead_id = db.Column(db.Integer, db.ForeignKey("leads.id"), nullable=False)
    lead = db.relationship("Lead", backref=db.backref("activities", lazy="dynamic"))

    activity_type_id = db.Column(db.Integer, db.ForeignKey("activity_types.id"))
    activity_type = db.relationship("ActivityType")

    subject = db.Column(db.String(200))
    outcome = db.Column(db.String(120))
    notes = db.Column(db.Text)

    activity_at = db.Column(db.DateTime, nullable=False)
    next_follow_up_at = db.Column(db.DateTime)

    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_by = db.relationship(
        "User",
        foreign_keys=[created_by_id],
        backref=db.backref("lead_activities_created", lazy="dynamic")
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    id = db.Column(db.Integer, primary_key=True)

    entity = db.Column(db.String(50), nullable=False)
    entity_id = db.Column(db.Integer)
    action = db.Column(db.String(50), nullable=False)
    field = db.Column(db.String(100))
    old_value = db.Column(db.Text)
    new_value = db.Column(db.Text)

    performed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    performed_by = db.relationship("User")

    performed_at = db.Column(db.DateTime, default=datetime.utcnow)


# -------------------------
# Pipeline
# -------------------------
class PipelineStage(db.Model):
    __tablename__ = "pipeline_stages"
    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(db.String(80), unique=True, nullable=False)
    color = db.Column(db.String(30), default="primary")
    probability = db.Column(db.Integer, default=10)
    sort_order = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)


class Opportunity(db.Model):
    __tablename__ = "opportunities"
    id = db.Column(db.Integer, primary_key=True)

    opp_code = db.Column(db.String(30), unique=True, index=True)
    title = db.Column(db.String(150), nullable=False)
    company = db.Column(db.String(120))
    contact_name = db.Column(db.String(120))
    contact_email = db.Column(db.String(120))
    contact_phone = db.Column(db.String(30))

    lead_id = db.Column(db.Integer, db.ForeignKey("leads.id"))
    lead = db.relationship("Lead")

    # ✅ NEW: link opportunity to client (recommended for reporting + conversion flow)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=True, index=True)
    client = db.relationship("Client", foreign_keys=[client_id])

    branch_id = db.Column(db.Integer, db.ForeignKey("client_branches.id"), nullable=True, index=True)
    branch = db.relationship("ClientBranch", foreign_keys=[branch_id])

    stage_id = db.Column(db.Integer, db.ForeignKey("pipeline_stages.id"))
    stage = db.relationship("PipelineStage")

    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    owner = db.relationship("User")

    expected_value = db.Column(db.Numeric(12, 2), default=0)
    expected_close_date = db.Column(db.Date)

    notes = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class OpportunityStageHistory(db.Model):
    __tablename__ = "opportunity_stage_history"
    id = db.Column(db.Integer, primary_key=True)

    opportunity_id = db.Column(db.Integer, db.ForeignKey("opportunities.id"), nullable=False)
    opportunity = db.relationship("Opportunity", backref=db.backref("stage_history", lazy="dynamic"))

    from_stage_id = db.Column(db.Integer, db.ForeignKey("pipeline_stages.id"))
    to_stage_id = db.Column(db.Integer, db.ForeignKey("pipeline_stages.id"))

    changed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    changed_by = db.relationship("User")

    changed_at = db.Column(db.DateTime, default=datetime.utcnow)
    remark = db.Column(db.String(255))


# -------------------------
# Quotes
# -------------------------
class QuoteStatus(db.Model):
    __tablename__ = "quote_statuses"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)  # Draft, Pending Approval, Approved, Selected, Rejected, Sent
    sort_order = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)


class Quote(db.Model):
    __tablename__ = "quotes"
    id = db.Column(db.Integer, primary_key=True)

    quote_code = db.Column(db.String(30), unique=True, index=True)
    version = db.Column(db.Integer, default=1)

    opportunity_id = db.Column(db.Integer, db.ForeignKey("opportunities.id"), nullable=False)
    opportunity = db.relationship("Opportunity", backref=db.backref("quotes", lazy="dynamic"))

    status_id = db.Column(db.Integer, db.ForeignKey("quote_statuses.id"))
    status = db.relationship("QuoteStatus")

    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_by = db.relationship(
        "User",
        foreign_keys=[created_by_id],
        backref=db.backref("quotes_created", lazy="dynamic")
    )

    # ✅ NEW: link quote to client once converted/selected
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=True, index=True)
    client = db.relationship("Client", foreign_keys=[client_id])

    branch_id = db.Column(db.Integer, db.ForeignKey("client_branches.id"), nullable=True, index=True)
    branch = db.relationship("ClientBranch", foreign_keys=[branch_id])

    subtotal = db.Column(db.Numeric(12, 2), default=0)
    discount = db.Column(db.Numeric(12, 2), default=0)
    tax = db.Column(db.Numeric(12, 2), default=0)
    cgst = db.Column(db.Numeric(12, 2), default=0)
    sgst = db.Column(db.Numeric(12, 2), default=0)
    igst = db.Column(db.Numeric(12, 2), default=0)
    total = db.Column(db.Numeric(12, 2), default=0)

    currency = db.Column(db.String(10), default="INR")
    valid_until = db.Column(db.Date)
    notes = db.Column(db.Text)
    customer_notes = db.Column(db.Text)

    estimated_closure_date = db.Column(db.Date, nullable=True)
    final_payment_date = db.Column(db.Date, nullable=True)

    proposal_terms = db.Column(db.Text, nullable=True)

    proposal_created_at = db.Column(db.DateTime, nullable=True)

    proposal_created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    proposal_created_by = db.relationship(
        "User",
        foreign_keys=[proposal_created_by_id],
        backref=db.backref("quotes_proposals_created", lazy="dynamic")
    )
    proposal_confirmed_at = db.Column(db.DateTime, nullable=True)
    proposal_confirmed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    proposal_confirmed_by = db.relationship(
        "User",
        foreign_keys=[proposal_confirmed_by_id],
        backref=db.backref("quotes_proposals_confirmed", lazy="dynamic")
    )

    # -------------------------
    # PI Request Workflow (Sales -> Finance)
    # -------------------------
    pi_requested_at = db.Column(db.DateTime, nullable=True)
    pi_requested_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    pi_requested_by = db.relationship(
        "User",
        foreign_keys=[pi_requested_by_id],
        backref=db.backref("pi_requests_made", lazy="dynamic")
    )

    pi_request_note = db.Column(db.Text, nullable=True)

    # Pending / Approved / Rejected (Approved = Finance generated PI)
    pi_request_status = db.Column(db.String(20), nullable=True, default=None)

    pi_generated_at = db.Column(db.DateTime, nullable=True)
    pi_generated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    pi_generated_by = db.relationship(
        "User",
        foreign_keys=[pi_generated_by_id],
        backref=db.backref("pi_generated", lazy="dynamic")
    )

    # -------------------------
    # Invoice Request Workflow (Sales -> Finance)
    # -------------------------
    invoice_requested_at = db.Column(db.DateTime, nullable=True)
    invoice_requested_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    invoice_requested_by = db.relationship(
        "User",
        foreign_keys=[invoice_requested_by_id],
        backref=db.backref("invoice_requests_made", lazy="dynamic")
    )

    invoice_request_note = db.Column(db.Text, nullable=True)

    # Pending / Approved / Rejected (Approved = Finance generated Invoice)
    invoice_request_status = db.Column(db.String(20), nullable=True, default=None)

    invoice_generated_at = db.Column(db.DateTime, nullable=True)
    invoice_generated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    invoice_generated_by = db.relationship(
        "User",
        foreign_keys=[invoice_generated_by_id],
        backref=db.backref("invoice_generated", lazy="dynamic")
    )

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    total_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    company_branch_id = db.Column(db.Integer, db.ForeignKey("company_branches.id"), nullable=True)
    company_branch = db.relationship("CompanyBranch")
    billing_state = db.Column(db.String(100), nullable=True)     # customer's state before client exists
    billing_gstin = db.Column(db.String(30), nullable=True)      # optional
    is_gst_applicable = db.Column(db.Boolean, default=True)

    def collected_amount(self):
        
        amt = (db.session.query(func.coalesce(func.sum(InvoicePayment.amount), 0))
            .join(Invoice, Invoice.id == InvoicePayment.invoice_id)
            .filter(Invoice.quote_id == self.id)
            .filter(InvoicePayment.status != "Rejected")
            .scalar()) or 0
        return Decimal(str(amt))

    def remaining_amount(self):
        return Decimal(str(self.total_amount or 0)) - Decimal(str(self.collected_amount() or 0))


class QuoteItem(db.Model):
    __tablename__ = "quote_items"
    id = db.Column(db.Integer, primary_key=True)

    quote_id = db.Column(db.Integer, db.ForeignKey("quotes.id"), nullable=False)
    quote = db.relationship("Quote", backref=db.backref("items", lazy="dynamic"))
    
    # ✅ NEW: service per line item (uses LeadService master)
    service_id = db.Column(db.Integer, db.ForeignKey("lead_services.id"), nullable=True, index=True)
    service = db.relationship("LeadService", foreign_keys=[service_id])

    item_name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    qty = db.Column(db.Numeric(10, 2), default=1)
    rate = db.Column(db.Numeric(12, 2), default=0)

    # ✅ NEW: billing cycle / item type
    # ONETIME | MONTHLY | HALF_YEARLY | ANNUAL
    billing_cycle = db.Column(db.String(20), nullable=False, default="ONETIME")

    amount = db.Column(db.Numeric(12, 2), default=0)
    sort_order = db.Column(db.Integer, default=0)


# -------------------------
# Approval Rules + Steps + Quote Approvals (Sequential)
# -------------------------
class ApprovalRule(db.Model):
    __tablename__ = "approval_rules"
    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(db.String(100), nullable=False)
    min_amount = db.Column(db.Numeric(12, 2), default=0)
    max_amount = db.Column(db.Numeric(12, 2))  # optional

    # backward compatibility; UI uses steps
    approver_role = db.Column(db.String(50), nullable=True)

    is_active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)


class ApprovalRuleStep(db.Model):
    __tablename__ = "approval_rule_steps"

    id = db.Column(db.Integer, primary_key=True)
    rule_id = db.Column(db.Integer, db.ForeignKey("approval_rules.id"), nullable=False)

    step_order = db.Column(db.Integer, nullable=False, default=1)

    approver_role = db.Column(db.String(100), nullable=True)
    approver_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    is_active = db.Column(db.Boolean, default=True)

    rule = db.relationship(
        "ApprovalRule",
        backref=db.backref("steps", lazy="dynamic", cascade="all, delete-orphan")
    )
    approver_user = db.relationship("User", foreign_keys=[approver_user_id])

    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class QuoteApproval(db.Model):
    __tablename__ = "quote_approvals"

    id = db.Column(db.Integer, primary_key=True)

    quote_id = db.Column(db.Integer, db.ForeignKey("quotes.id"), nullable=False)
    rule_id = db.Column(db.Integer, db.ForeignKey("approval_rules.id"), nullable=True)

    rule_step_id = db.Column(db.Integer, db.ForeignKey("approval_rule_steps.id"), nullable=True)
    step_order = db.Column(db.Integer, nullable=False, default=1)

    approver_role = db.Column(db.String(100), nullable=True)
    approver_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    status = db.Column(db.String(20), nullable=False, default="WAITING")

    remark = db.Column(db.Text, nullable=True)
    acted_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    acted_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    quote = db.relationship(
        "Quote",
        backref=db.backref("approvals", lazy="dynamic", cascade="all, delete-orphan")
    )
    rule = db.relationship("ApprovalRule")
    step = db.relationship("ApprovalRuleStep")
    acted_by = db.relationship("User", foreign_keys=[acted_by_id])
    approver_user = db.relationship("User", foreign_keys=[approver_user_id])
# -------------------------
# Payment Collections
# -------------------------

# -------------------------
# Payment Collections
# -------------------------
class PaymentCollection(db.Model):
    __tablename__ = "payment_collections"   # ✅ better plural; you can keep old name if already migrated

    id = db.Column(db.Integer, primary_key=True)

    # ✅ FIX: your table is "quotes" not "quote"
    quote_id = db.Column(db.Integer, db.ForeignKey("quotes.id"), nullable=False, index=True)

    # ✅ FIX: your table is "leads" not "lead"
    lead_id = db.Column(db.Integer, db.ForeignKey("leads.id"), nullable=True, index=True)

    payment_date = db.Column(db.Date, nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)

    transfer_type = db.Column(db.String(50), nullable=False)
    reference = db.Column(db.String(255), nullable=True)

    status = db.Column(db.String(20), nullable=False, default="Pending")  # Pending/Verified/Rejected

    # ✅ FIX: your table is "users" not "user"
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    verified_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    verified_at = db.Column(db.DateTime, nullable=True)
    finance_remarks = db.Column(db.String(255), nullable=True)

    quote = db.relationship("Quote", backref=db.backref("collections", lazy="dynamic"))
    lead = db.relationship("Lead", foreign_keys=[lead_id])
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    verified_by = db.relationship("User", foreign_keys=[verified_by_id])

class ClientDocument(db.Model):
    __tablename__ = "client_documents"  # ✅ recommended plural (use your existing name if already migrated)

    id = db.Column(db.Integer, primary_key=True)

    # ✅ FIX: correct FK target table = clients.id
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)

    # ✅ FIX: correct FK target table = quotes.id
    quote_id = db.Column(db.Integer, db.ForeignKey("quotes.id"), nullable=True, index=True)  # NULL = overall

    document_name = db.Column(db.String(200), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    expiry_date = db.Column(db.Date, nullable=False)

    file_name = db.Column(db.String(255), nullable=False)
    file_path = db.Column(db.String(500), nullable=False)

    # ✅ FIX: uploader should be users.id (since your auth model is User)
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # ✅ relationships
    client = db.relationship("Client", backref=db.backref("documents", lazy="dynamic", cascade="all, delete-orphan"))
    quote = db.relationship("Quote", backref=db.backref("documents", lazy="dynamic"))
    uploaded_by = db.relationship("User", foreign_keys=[uploaded_by_id])

class Industry(db.Model):
    __tablename__ = "industries"
    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(db.String(120), unique=True, nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Company(db.Model):
    __tablename__ = "companies"
    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(db.String(200), nullable=False, unique=True)
    pan = db.Column(db.String(20), nullable=True)
    logo_path = db.Column(db.String(500), nullable=True)  # stored file path

    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    branches = db.relationship(
        "CompanyBranch",
        back_populates="company",
        cascade="all, delete-orphan",
        lazy="dynamic"
    )


class CompanyBranch(db.Model):
    __tablename__ = "company_branches"
    id = db.Column(db.Integer, primary_key=True)

    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False, index=True)
    company = db.relationship("Company", back_populates="branches")

    branch_name = db.Column(db.String(200), nullable=False)
    branch_address = db.Column(db.Text, nullable=True)
    state = db.Column(db.String(100), nullable=True)
    gst_no = db.Column(db.String(30), nullable=True)

    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("company_id", "branch_name", name="uq_company_branch_name"),
    )



class ProformaInvoice(db.Model):
    __tablename__ = "proforma_invoices"
    id = db.Column(db.Integer, primary_key=True)

    pi_no = db.Column(db.String(40), unique=True, index=True, nullable=False)
    pi_date = db.Column(db.Date, nullable=False)

    # link chain
    quote_id = db.Column(db.Integer, db.ForeignKey("quotes.id"), nullable=False, index=True)
    quote = db.relationship("Quote", backref=db.backref("proformas", lazy="dynamic"))

    # optional but helpful snapshots (client info is derived from quote too)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=True, index=True)
    client = db.relationship("Client", foreign_keys=[client_id])

    client_branch_id = db.Column(db.Integer, db.ForeignKey("client_branches.id"), nullable=True, index=True)
    client_branch = db.relationship("ClientBranch", foreign_keys=[client_branch_id])

    company_branch_id = db.Column(db.Integer, db.ForeignKey("company_branches.id"), nullable=True, index=True)
    company_branch = db.relationship("CompanyBranch", foreign_keys=[company_branch_id])

    currency = db.Column(db.String(10), default="INR")

    subtotal = db.Column(db.Numeric(12, 2), default=0)
    discount = db.Column(db.Numeric(12, 2), default=0)
    tax = db.Column(db.Numeric(12, 2), default=0)
    cgst = db.Column(db.Numeric(12, 2), default=0)
    sgst = db.Column(db.Numeric(12, 2), default=0)
    igst = db.Column(db.Numeric(12, 2), default=0)
    total_amount = db.Column(db.Numeric(12, 2), default=0)

    notes = db.Column(db.Text, nullable=True)
    terms = db.Column(db.Text, nullable=True)

    status = db.Column(db.String(20), default="Draft")  
    # Draft / Issued / Cancelled / Converted

    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class Invoice(db.Model):
    __tablename__ = "invoices"
    id = db.Column(db.Integer, primary_key=True)

    invoice_no = db.Column(db.String(40), unique=True, index=True, nullable=False)
    invoice_date = db.Column(db.Date, nullable=False)

    # link chain
    pi_id = db.Column(db.Integer, db.ForeignKey("proforma_invoices.id"), nullable=False, index=True)
    pi = db.relationship("ProformaInvoice", backref=db.backref("invoices", lazy="dynamic"))

    quote_id = db.Column(db.Integer, db.ForeignKey("quotes.id"), nullable=False, index=True)
    quote = db.relationship("Quote", backref=db.backref("invoices", lazy="dynamic"))

    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=True, index=True)
    client = db.relationship("Client", foreign_keys=[client_id])

    client_branch_id = db.Column(db.Integer, db.ForeignKey("client_branches.id"), nullable=True, index=True)
    client_branch = db.relationship("ClientBranch", foreign_keys=[client_branch_id])

    company_branch_id = db.Column(db.Integer, db.ForeignKey("company_branches.id"), nullable=True, index=True)
    company_branch = db.relationship("CompanyBranch", foreign_keys=[company_branch_id])

    currency = db.Column(db.String(10), default="INR")

    subtotal = db.Column(db.Numeric(12, 2), default=0)
    discount = db.Column(db.Numeric(12, 2), default=0)
    tax = db.Column(db.Numeric(12, 2), default=0)
    cgst = db.Column(db.Numeric(12, 2), default=0)
    sgst = db.Column(db.Numeric(12, 2), default=0)
    igst = db.Column(db.Numeric(12, 2), default=0)
    total_amount = db.Column(db.Numeric(12, 2), default=0)

    notes = db.Column(db.Text, nullable=True)
    terms = db.Column(db.Text, nullable=True)

    status = db.Column(db.String(20), default="Unpaid")
    # Unpaid / Partially Paid / Paid / Cancelled

    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    credit_days = db.Column(db.Integer, nullable=True, default=0)
    due_date = db.Column(db.Date, nullable=True, index=True)

    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=True, index=True)
    project = db.relationship("Project", foreign_keys=[project_id])

    def collected_amount(self):
        return (db.session.query(func.coalesce(func.sum(InvoicePayment.amount), 0))
                .filter(InvoicePayment.invoice_id == self.id)
                .filter(InvoicePayment.status != "Rejected")
                .scalar()) or 0

    def remaining_amount(self):
        return Decimal(str(self.total_amount)) - Decimal(str(self.collected_amount()))
    

class InvoicePayment(db.Model):
    __tablename__ = "invoice_payments"

    id = db.Column(db.Integer, primary_key=True)

    invoice_id = db.Column(db.Integer, db.ForeignKey("invoices.id"), nullable=False, index=True)
    invoice = db.relationship("Invoice", backref=db.backref("payments", lazy="dynamic"))

    payment_date = db.Column(db.Date, nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)

    transfer_type = db.Column(db.String(50), nullable=False)
    reference = db.Column(db.String(255), nullable=True)

    status = db.Column(db.String(20), nullable=False, default="Pending")  # Pending/Verified/Rejected

    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    verified_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    verified_at = db.Column(db.DateTime, nullable=True)
    finance_remarks = db.Column(db.String(255), nullable=True)

    created_by = db.relationship("User", foreign_keys=[created_by_id])
    verified_by = db.relationship("User", foreign_keys=[verified_by_id])


class Project(db.Model):
    __tablename__ = "projects"
    id = db.Column(db.Integer, primary_key=True)

    project_code = db.Column(db.String(40), unique=True, index=True, nullable=False)
    name = db.Column(db.String(200), nullable=False)

    quote_id = db.Column(db.Integer, db.ForeignKey("quotes.id"), nullable=False, index=True)
    quote = db.relationship("Quote", backref=db.backref("project", uselist=False))

    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=True, index=True)
    client = db.relationship("Client", foreign_keys=[client_id])

    branch_id = db.Column(db.Integer, db.ForeignKey("client_branches.id"), nullable=True, index=True)
    branch = db.relationship("ClientBranch", foreign_keys=[branch_id])

    # AM responsibility (optional now; we can map from client later)
    account_manager_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    account_manager = db.relationship("User", foreign_keys=[account_manager_user_id])

    # Snapshots for reporting
    currency = db.Column(db.String(10), default="INR")
    contract_value = db.Column(db.Numeric(12, 2), default=0)  # typically quote.total_amount at create time

    # Margin tracking (computed)
    total_cost = db.Column(db.Numeric(12, 2), default=0)
    margin_amount = db.Column(db.Numeric(12, 2), default=0)
    margin_percent = db.Column(db.Numeric(6, 2), default=0)

    # Flagging / reason
    margin_flag = db.Column(db.Boolean, default=False)  # True if < threshold (e.g., 50%)
    margin_reason = db.Column(db.String(255), nullable=True)

    status = db.Column(db.String(20), default="Active")  # Active/Closed/Hold

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

class ProjectCost(db.Model):
    __tablename__ = "project_costs"
    id = db.Column(db.Integer, primary_key=True)

    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False, index=True)
    project = db.relationship("Project", backref=db.backref("costs", lazy="dynamic", cascade="all, delete-orphan"))

    
    cost_date = db.Column(db.Date, nullable=False, default=date.today)
    cost_head = db.Column(db.String(120), nullable=False)  # e.g. Vendor, Resource, Travel, Tools
    vendor_name = db.Column(db.String(200), nullable=True)

    amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)

    notes = db.Column(db.Text, nullable=True)

    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

class MarginSettings(db.Model):
    __tablename__ = "margin_settings"
    id = db.Column(db.Integer, primary_key=True)

    threshold_percent = db.Column(db.Numeric(6, 2), nullable=False, default=50.00)
    is_active = db.Column(db.Boolean, default=True)

    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    updated_by = db.relationship("User", foreign_keys=[updated_by_id])

    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

class Cluster(db.Model):
    __tablename__ = "clusters"
    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(db.String(120), nullable=False, unique=True)
    head_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    head_user = db.relationship("User", foreign_keys=[head_user_id])

    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)