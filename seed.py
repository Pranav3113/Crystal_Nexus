from app import create_app, db
from app.models import User, Role, Permission, PipelineStage, QuoteStatus, ActivityType, ApprovalRule, ApprovalRuleStep

app = create_app()
with app.app_context():
    admin_role = Role.query.filter_by(name="Admin").first() or Role(name="Admin")
    db.session.add(admin_role)

    # Basic permissions (we’ll expand later)
    perm_codes = [
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
        "quotes.view",
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
        "company.manage",          # Company + Branch master
        "company.view",  
    ]
    
    for code in perm_codes:
        p = Permission.query.filter_by(code=code).first()
        if not p:
            db.session.add(Permission(code=code, description=code))

    from app.models import LeadStatus, LeadSource

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
    
    # --- Approval Rules + Steps ---
    default_rules = [
        ("Default Approval (>= 1)", 1, None, "Admin", 1),
    ]

    for r_name, min_amt, max_amt, approver_role, order in default_rules:
        rule = ApprovalRule.query.filter_by(name=r_name).first()
        if not rule:
            rule = ApprovalRule(
                name=r_name,
                min_amount=min_amt,
                max_amount=max_amt,
                approver_role=approver_role,   # backward compatibility only
                sort_order=order,
                is_active=True
            )
            db.session.add(rule)
            db.session.flush()

        # Ensure at least one step exists
        if rule.steps.count() == 0:
            db.session.add(ApprovalRuleStep(
                rule_id=rule.id,
                step_order=1,
                approver_role=approver_role,
                approver_user_id=None,
                is_active=True
            ))

    db.session.commit()

    admin_role.permissions = Permission.query.all()
    db.session.commit()

    u = User.query.filter_by(email="admin@crystalnexus.local").first()
    if not u:
        u = User(email="admin@crystalnexus.local", name="System Admin", role=admin_role)
        u.set_password("Admin@1234")
        db.session.add(u)
        db.session.commit()

    print("Seeded admin: admin@crystalnexus.local / Admin@1234")