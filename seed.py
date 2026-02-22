from app import create_app, db

# core models
from app.models import (
    User, Role, Permission,
    LeadStatus, LeadSource,
    PipelineStage, QuoteStatus, ActivityType,
    ApprovalRule, ApprovalRuleStep,
    Menu, SubMenu,   # ✅ IMPORTANT (menus + submenus)
)

app = create_app()


def upsert(model, obj_id: int, **fields):
    """Upsert by explicit integer ID."""
    row = model.query.get(obj_id)
    if row:
        for k, v in fields.items():
            setattr(row, k, v)
        return row
    row = model(id=obj_id, **fields)
    db.session.add(row)
    return row


with app.app_context():

    # -------------------------
    # 1) Admin role
    # -------------------------
    admin_role = Role.query.filter_by(name="Admin").first()
    if not admin_role:
        admin_role = Role(name="Admin")
        db.session.add(admin_role)
        db.session.flush()

    # -------------------------
    # 2) Permissions
    #    (your list + submenu permission codes from dump)
    # -------------------------
    perm_codes = set([
        "admin.dashboard.view",
        "leads.view",
        "leads.create",
        "leads.edit",
        "masters.manage",
        "activities.view",
        "activities.create",
        "admin.audit.view",
        "leads.assign",
        "pipeline.view",
        "pipeline.create",
        "pipeline.edit",
        "pipeline.move",
        "pipeline.manage_stages",
        "quotes.view",
        "quotes.create",
        "quotes.edit",
        "quotes.request_approval",
        "quotes.approve",
        "quotes.send",
        "approval_rules.manage",
        "users.manage",
        "designations.manage",
        "roles.manage",
        "permissions.manage",
        "clients.manage",
        "payments.add",
        "payments.admin",
        "payments.verify",
        "payments.view",
        "industries.manage",
        "quotes.proposals_sent.view",
        "company.manage",
        "company.view",
    ])

    # ✅ extra permissions referenced in your menus_dump.sql
    perm_codes.update([
        "menus.manage",
        "currencies.manage",
        "clusters.manage",
        "proforma.requests.view",
        "proforma.view",
        "invoices.requests.view",
        "invoices.view",
    ])

    for code in sorted(perm_codes):
        if not Permission.query.filter_by(code=code).first():
            db.session.add(Permission(code=code, description=code))

    db.session.commit()

    # -------------------------
    # 3) Defaults (Lead / Source / Activity / Pipeline / Quote statuses)
    # -------------------------
    defaults_statuses = [
        ("New", "primary", 1),
        ("Contacted", "info", 2),
        ("Qualified", "success", 3),
        ("Lost", "secondary", 90),
    ]
    for name, color, order in defaults_statuses:
        if not LeadStatus.query.filter_by(name=name).first():
            db.session.add(LeadStatus(name=name, color=color, sort_order=order))

    defaults_sources = [
        ("Website", 1),
        ("Referral", 2),
        ("Cold Call", 3),
        ("Walk-in", 4),
    ]
    for name, order in defaults_sources:
        if not LeadSource.query.filter_by(name=name).first():
            db.session.add(LeadSource(name=name, sort_order=order))

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
        ("Rejected", 4),
        ("Sent", 5),
    ]
    for name, order in default_quote_statuses:
        if not QuoteStatus.query.filter_by(name=name).first():
            db.session.add(QuoteStatus(name=name, sort_order=order, is_active=True))

    db.session.commit()

    # -------------------------
    # 4) Approval rule + step
    # -------------------------
    rule_name = "Default Approval (>= 1)"
    rule = ApprovalRule.query.filter_by(name=rule_name).first()
    if not rule:
        rule = ApprovalRule(
            name=rule_name,
            min_amount=1,
            max_amount=None,
            approver_role="Admin",  # backward compatibility
            sort_order=1,
            is_active=True,
        )
        db.session.add(rule)
        db.session.flush()

    # Ensure at least one step exists
    if rule.steps.count() == 0:
        db.session.add(ApprovalRuleStep(
            rule_id=rule.id,
            step_order=1,
            approver_role="Admin",
            approver_user_id=None,
            is_active=True
        ))

    db.session.commit()

    # -------------------------
    # 5) ✅ Menus + Submenus (from menus_dump.sql)
    # -------------------------
    menus = [
        (10, "Dashboard", "speedometer2", 5, True),
        (11, "Sales", "bar-chart", 10, True),
        (12, "Quotes", "receipt", 20, True),
        (13, "Finance", "cash-stack", 30, True),
        (14, "Masters", "sliders", 40, True),
        (15, "Admin", "gear", 90, True),
        (16, "System", "shield-check", 100, True),
    ]

    for mid, title, icon, sort_order, is_active in menus:
        upsert(Menu, mid,
               title=title, icon=icon, sort_order=sort_order, is_active=is_active)

    submenus = [
        (60, 10, "Dashboard", "admin.dashboard", None, None, 1, True, "admin.dashboard.view"),
        (61, 11, "Leads", "leads.list_leads", None, None, 1, True, "leads.view"),
        (62, 11, "Pipeline", "pipeline.board", None, None, 2, True, "pipeline.view"),
        (63, 11, "Clients", "clients.list_clients", None, None, 3, True, "clients.manage"),
        (64, 12, "Quotes", "quotes.list_quotes", None, None, 1, True, "quotes.view"),
        (65, 12, "Proposals Sent", "quotes.sent_proposals", None, None, 2, True, "quotes.proposals_sent.view"),
        (66, 12, "Approvals Inbox", "quotes.approvals_inbox", None, None, 3, True, "quotes.approve"),
        (67, 12, "Approval Rules", "quotes.approval_rules_master", None, None, 4, True, "approval_rules.manage"),
        (68, 13, "PI Requests", "proforma.pi_requests", None, None, 1, True, "proforma.requests.view"),
        (69, 13, "Proforma Invoices", "proforma.list_pi", None, None, 2, True, "proforma.view"),
        (70, 13, "Invoice Requests", "invoices.invoice_requests", None, None, 3, True, "invoices.requests.view"),
        (71, 13, "Invoices", "invoices.list_invoices", None, None, 4, True, "invoices.view"),
        (72, 13, "Payments Queue", "payments.finance_payment_queue", None, None, 5, True, "payments.verify"),
        (73, 14, "Lead Status", "admin.lead_status_master", None, None, 1, True, "masters.manage"),
        (74, 14, "Lead Source", "admin.lead_source_master", None, None, 2, True, "masters.manage"),
        (75, 14, "Activity Types", "admin.activity_type_master", None, None, 3, True, "masters.manage"),
        (76, 14, "Industries", "industries.industries_master", None, None, 4, True, "industries.manage"),
        (77, 14, "Company", "company_master.company_master", None, None, 5, True, "company.view"),
        (78, 15, "User Master", "user_master.users_master", None, None, 1, True, "users.manage"),
        (79, 15, "Designations", "designations.designation_master", None, None, 2, True, "designations.manage"),
        (80, 15, "Roles", "rbac.roles_master", None, None, 3, True, "roles.manage"),
        (81, 15, "Permissions", "rbac.permissions_master", None, None, 4, True, "permissions.manage"),
        (82, 15, "Menu Management", "menu_master.menu_management", None, None, 5, True, "menus.manage"),
        (83, 16, "Audit Logs", "admin.audit_logs", None, None, 1, True, "admin.audit.view"),
        (84, 16, "Logout", "auth.logout", None, None, 999, True, None),

        # extra routes (url-based) from dump
        (86, 14, "Currency Master", "currencies.currencies_master", None, None, 1, True, "currencies.manage"),
        (87, 10, "Cluster Productivity", None, "/reports/cluster/productivity", None, 1, True, "admin.dashboard.view"),
        (89, 10, "Collections & Aging", None, "/reports/cluster/collections-aging", None, 1, True, None),
        (90, 10, "Cluster Margin Quality", None, "/reports/cluster/margin-quality", None, 1, True, "admin.dashboard.view"),
        (91, 14, "Margin Settings", None, "/admin/margin-settings", None, 1, True, "masters.manage"),
        (92, 10, "Pipeline vs Conversion", None, "/reports/cluster/pipeline-conversion", None, 1, True, "admin.dashboard.view"),
        (93, 10, "Account Health", None, "/reports/cluster/account-health", None, 1, True, "admin.dashboard.view"),
        (94, 14, "Cluster Master", "admin.cluster_master", None, None, 1, True, "clusters.manage"),
        (95, 10, "My Dashboard", None, "/reports/my-dashboard", None, 1, True, None),
    ]

    for sid, menu_id, title, endpoint, url, icon, sort_order, is_active, perm in submenus:
        upsert(SubMenu, sid,
               menu_id=menu_id,
               title=title,
               endpoint=endpoint,
               url=url,
               icon=icon,
               sort_order=sort_order,
               is_active=is_active,
               permission_code=perm)

    db.session.commit()

    # -------------------------
    # 6) Give Admin all permissions
    # -------------------------
    admin_role.permissions = Permission.query.all()
    db.session.commit()

    # -------------------------
    # 7) Create admin user
    # -------------------------
    u = User.query.filter_by(email="admin@crystalnexus.local").first()
    if not u:
        u = User(email="admin@crystalnexus.local", name="System Admin", role=admin_role, is_active=True)
        u.set_password("Admin@1234")
        db.session.add(u)
        db.session.commit()

    print("✅ Seed completed.")
    print("✅ Admin: admin@crystalnexus.local / Admin@1234")