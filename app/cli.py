from sqlalchemy import text
from . import db
from .models import (
    User, Role, Permission,
    LeadStatus, LeadSource,
    PipelineStage, QuoteStatus, ActivityType,
    ApprovalRule, ApprovalRuleStep, Industry,
    Menu, SubMenu, Currency
)


# =========================================================
# Seed All
# =========================================================
def seed_all():
    # ---- Role ----
    admin_role = Role.query.filter_by(name="Admin").first() or Role(name="Admin")
    db.session.add(admin_role)
    db.session.flush()

    perm_codes = [
        "admin.dashboard.view",

        # leads
        "leads.view", "leads.create", "leads.edit",
        "leads.assign", "leads.assign_any", "leads.view_all",

        # masters
        "masters.manage",
        "lead_services.manage",
        "clusters.manage",

        # activities (future)
        "activities.view", "activities.create",

        # audit
        "admin.audit.view",

        # pipeline
        "pipeline.view", "pipeline.create", "pipeline.edit",
        "pipeline.move", "pipeline.manage_stages",

        # quotes
        "quotes.view", "quotes.create", "quotes.edit",
        "quotes.request_approval", "quotes.approve", "quotes.send",
        "quotes.proposals_sent.view",

        "approval_rules.manage",

        # clients
        "clients.manage",

        # proforma + invoices
        "proforma.request", "proforma.generate", "proforma.requests.view",
        "proforma.view_all",

        "invoices.request", "invoices.requests.view", "invoices.generate",
        "invoices.view", "invoices.manage",
        "invoices.view_all",

        "proforma.view", "proforma.create",

        # payments
        "payments.add",
        "payments.admin", "payments.verify", "payments.view",

        # masters extra
        "industries.manage",

        # company master
        "company.manage", "company.view",

        # admin access control
        "users.manage", "designations.manage",
        "roles.manage", "permissions.manage",

        # menu master
        "menus.manage",
        
        "currencies.manage",
        
        "projects.create",
        "projects.view",
        "projects.cost.delete",
        "projects.cost.add",
    ]

    # Seed permissions
    for code in perm_codes:
        if not Permission.query.filter_by(code=code).first():
            db.session.add(Permission(code=code, description=code))

    # ---- Masters ----
    defaults_statuses = [
        ("New", "primary", 1),
        ("Contacted", "info", 2),
        ("Qualified", "success", 3),
        ("Lost", "secondary", 90),
    ]
    for name, color, order in defaults_statuses:
        if not LeadStatus.query.filter_by(name=name).first():
            db.session.add(LeadStatus(name=name, color=color, sort_order=order, is_active=True))

    defaults_sources = [
        ("Website", 1),
        ("Referral", 2),
        ("Cold Call", 3),
        ("Walk-in", 4),
    ]
    for name, order in defaults_sources:
        if not LeadSource.query.filter_by(name=name).first():
            db.session.add(LeadSource(name=name, sort_order=order, is_active=True))

    default_industries = [
        ("Information Technology", 1),
        ("Software / SaaS", 2),
        ("Manufacturing", 3),
        ("Healthcare", 4),
        ("Pharmaceuticals", 5),
        ("Education", 6),
        ("Retail", 7),
        ("E-Commerce", 8),
        ("Real Estate", 9),
        ("Finance", 10),
        ("Banking", 11),
        ("Insurance", 12),
        ("Logistics", 13),
        ("Construction", 14),
        ("Hospitality", 15),
        ("Media & Entertainment", 16),
        ("Telecom", 17),
        ("Automobile", 18),
        ("FMCG", 19),
        ("Government", 20),
    ]
    for name, order in default_industries:
        if not Industry.query.filter_by(name=name).first():
            db.session.add(Industry(name=name, sort_order=order, is_active=True))

    

    default_currencies = [
        ("INR", "Indian Rupee", "₹", True, 1),
        ("USD", "US Dollar", "$", False, 2),
        ("EUR", "Euro", "€", False, 3),
        ("GBP", "British Pound", "£", False, 4),
    ]
    for code, name, sym, gst, order in default_currencies:
        if not Currency.query.filter_by(code=code).first():
            db.session.add(Currency(code=code, name=name, symbol=sym, gst_applicable=gst, sort_order=order, is_active=True))
            
    default_activity_types = [
        ("Call", "telephone", 1),
        ("Email", "envelope", 2),
        ("Meeting", "calendar-event", 3),
        ("WhatsApp", "chat-dots", 4),
        ("Site Visit", "geo-alt", 5),
    ]
    for name, icon, order in default_activity_types:
        if not ActivityType.query.filter_by(name=name).first():
            db.session.add(ActivityType(name=name, icon=icon, sort_order=order, is_active=True))

    default_stages = [
        ("Prospect", "secondary", 10, 1),
        ("Qualified", "info", 30, 2),
        ("Proposal", "primary", 50, 3),
        ("Negotiation", "warning", 70, 4),
        ("Won", "success", 100, 90),
        ("Lost", "dark", 0, 99),
    ]
    for name, color, prob, order in default_stages:
        if not PipelineStage.query.filter_by(name=name).first():
            db.session.add(PipelineStage(name=name, color=color, probability=prob, sort_order=order, is_active=True))

    default_quote_statuses = [
        ("Draft", 1),
        ("Pending Approval", 2),
        ("Approved", 3),
        ("Selected", 4),
        ("Rejected", 5),
        ("Sent", 6),
    ]
    for name, order in default_quote_statuses:
        if not QuoteStatus.query.filter_by(name=name).first():
            db.session.add(QuoteStatus(name=name, sort_order=order, is_active=True))

    # ---- Approval Rules + Steps ----
    default_rules = [("Default Approval (>= 1)", 1, None, "Admin", 1)]
    for r_name, min_amt, max_amt, approver_role, order in default_rules:
        rule = ApprovalRule.query.filter_by(name=r_name).first()
        if not rule:
            rule = ApprovalRule(
                name=r_name,
                min_amount=min_amt,
                max_amount=max_amt,
                approver_role=approver_role,
                sort_order=order,
                is_active=True
            )
            db.session.add(rule)
            db.session.flush()

        if rule.steps.count() == 0:
            db.session.add(ApprovalRuleStep(
                rule_id=rule.id,
                step_order=1,
                approver_role=approver_role,
                approver_user_id=None,
                is_active=True
            ))

    db.session.commit()

    # Admin has all permissions
    admin_role.permissions = Permission.query.all()
    db.session.commit()

    # ---- Admin User ----
    u = User.query.filter_by(email="admin@crystalnexus.local").first()
    if not u:
        u = User(
            email="admin@crystalnexus.local",
            name="System Admin",
            role=admin_role,
            auth_provider="LOCAL",
            is_active=True
        )
        u.set_password("Admin@1234")
        db.session.add(u)
        db.session.commit()

    # ✅ Seed menus at the end
    seed_menus()
    db.session.commit()


# =========================================================
# Menu Seeding (covers EVERY permission)
# =========================================================
def seed_menus():
    # If menus already exist, don't duplicate.
    # (During dev, easiest is: TRUNCATE menu + submenu then run `flask seed`)
    if Menu.query.count() > 0:
        return

    # -------------------------
    # MENUS
    # -------------------------
    m_dash   = Menu(title="Dashboard", icon="speedometer2", sort_order=5,   is_active=True)
    m_sales  = Menu(title="Sales",     icon="bar-chart",    sort_order=10,  is_active=True)
    m_quotes = Menu(title="Quotes",    icon="receipt",      sort_order=20,  is_active=True)
    m_fin    = Menu(title="Finance",   icon="cash-stack",   sort_order=30,  is_active=True)
    m_master = Menu(title="Masters",   icon="sliders",      sort_order=40,  is_active=True)
    m_admin  = Menu(title="Admin",     icon="gear",         sort_order=90,  is_active=True)
    m_system = Menu(title="System",    icon="shield-check", sort_order=100, is_active=True)

    db.session.add_all([m_dash, m_sales, m_quotes, m_fin, m_master, m_admin, m_system])
    db.session.flush()

    # -------------------------
    # Screen map (ONE row per screen)
    # permission_code here should be the permission that grants access to that screen.
    # -------------------------
    screens = [
        # Dashboard
        (m_dash.id,   "Dashboard",        "admin.dashboard",                 "admin.dashboard.view"),

        # Sales
        (m_sales.id,  "Leads",            "leads.list_leads",                "leads.view"),
        (m_sales.id,  "Pipeline",         "pipeline.board",                  "pipeline.view"),
        (m_sales.id,  "Clients",          "clients.list_clients",            "clients.manage"),
        (m_dash.id,   "Projects",         "projects.list_projects",         "projects.view"),

        # Quotes
        (m_quotes.id, "Quotes",           "quotes.list_quotes",              "quotes.view"),
        (m_quotes.id, "Proposals Sent",   "quotes.sent_proposals",           "quotes.proposals_sent.view"),
        (m_quotes.id, "Approvals Inbox",  "quotes.approvals_inbox",          "quotes.approve"),
        (m_quotes.id, "Approval Rules",   "quotes.approval_rules_master",    "approval_rules.manage"),

        # Finance
        (m_fin.id,    "PI Requests",      "proforma.pi_requests",            "proforma.requests.view"),
        (m_fin.id,    "Proforma Invoices","proforma.list_pi",                "proforma.view"),
        (m_fin.id,    "Invoice Requests", "invoices.invoice_requests",       "invoices.requests.view"),
        (m_fin.id,    "Invoices",         "invoices.list_invoices",          "invoices.view"),
        (m_fin.id,    "Payments Queue",   "payments.finance_payment_queue",  "payments.verify"),

        # Masters
        (m_master.id, "Lead Status",      "admin.lead_status_master",        "masters.manage"),
        (m_master.id, "Lead Source",      "admin.lead_source_master",        "masters.manage"),
        (m_master.id, "Activity Types",   "admin.activity_type_master",      "masters.manage"),
        (m_master.id, "Industries",       "industries.industries_master",    "industries.manage"),
        (m_master.id, "Company",          "company_master.company_master",   "company.view"),

        # Admin
        (m_admin.id,  "User Master",      "user_master.users_master",        "users.manage"),
        (m_admin.id,  "Designations",     "designations.designation_master", "designations.manage"),
        (m_admin.id,  "Roles",            "rbac.roles_master",               "roles.manage"),
        (m_admin.id,  "Permissions",      "rbac.permissions_master",         "permissions.manage"),
        (m_admin.id,  "Menu Management",  "menu_master.menu_management",     "menus.manage"),

        # System
        (m_system.id, "Audit Logs",       "admin.audit_logs",                "admin.audit.view"),
    ]

    # Create submenus (unique, clean titles)
    sort_map = {}
    for menu_id, title, endpoint, perm_code in screens:
        sort_map.setdefault(menu_id, 0)
        sort_map[menu_id] += 1
        db.session.add(SubMenu(
            menu_id=menu_id,
            title=title,
            endpoint=endpoint,
            url=None,
            icon=None,
            sort_order=sort_map[menu_id],
            is_active=True,
            permission_code=perm_code
        ))

    # Logout
    db.session.add(SubMenu(
        menu_id=m_system.id,
        title="Logout",
        endpoint="auth.logout",
        permission_code=None,
        sort_order=999,
        is_active=True
    ))

# =========================================================
# Reset / CLI registration
# =========================================================
def wipe_all_tables():
    conn = db.engine.connect()
    trans = conn.begin()
    try:
        conn.execute(text("SET FOREIGN_KEY_CHECKS=0;"))
        for table in reversed(db.metadata.sorted_tables):
            conn.execute(text(f"TRUNCATE TABLE `{table.name}`;"))
        conn.execute(text("SET FOREIGN_KEY_CHECKS=1;"))
        trans.commit()
    except Exception:
        trans.rollback()
        raise
    finally:
        conn.close()


def register_cli(app):
    @app.cli.command("seed")
    def seed_cmd():
        seed_all()
        print("✅ Seed completed")

    @app.cli.command("reset-db")
    def reset_db_cmd():
        wipe_all_tables()
        seed_all()
        print("✅ DB wiped + reseeded")