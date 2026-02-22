from sqlalchemy import create_engine, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import sessionmaker
from werkzeug.security import generate_password_hash

from . import db
from .models import Role, Permission, User


def _ensure_database_exists(db_uri: str):
    url = make_url(db_uri)

    if url.get_backend_name() not in ("mysql", "mysql+pymysql"):
        return

    db_name = url.database
    if not db_name:
        raise ValueError("DB URI must include database name")

    server_url = url.set(database=None)

    # ✅ MySQL CREATE DATABASE should run in AUTOCOMMIT mode
    engine = create_engine(server_url, pool_pre_ping=True, isolation_level="AUTOCOMMIT")

    with engine.connect() as conn:
        conn.execute(
            text(
                f"CREATE DATABASE IF NOT EXISTS `{db_name}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        )
        # ✅ no conn.commit() here


def _tenant_tables_only():
    """
    Return ONLY tenant tables (exclude any bind_key='platform' tables).
    """
    return [
        t for t in db.metadata.sorted_tables
        if t.info.get("bind_key") != "platform"
    ]


def provision_tenant(db_uri: str, admin_email: str, admin_name: str, admin_password: str):
    _ensure_database_exists(db_uri)

    # ✅ Ensure all tenant models are loaded into db.metadata before create_all
    # If you have multiple model modules, import them here too.
    import app.models  # noqa: F401

    engine = create_engine(db_uri, pool_pre_ping=True, pool_recycle=1800)

    # ✅ Create only tenant tables (exclude platform tables)
    db.metadata.create_all(bind=engine, tables=_tenant_tables_only())

    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    try:
        admin_role = session.query(Role).filter_by(name="Admin").first()
        if not admin_role:
            admin_role = Role(name="Admin")
            session.add(admin_role)
            session.flush()

        perm_codes = [
            "admin.dashboard.view",
            "leads.view", "leads.create", "leads.edit",
            "pipeline.view", "quotes.view",
            "users.manage", "roles.manage", "permissions.manage",
            "masters.manage",
        ]

        existing = {
            p.code for p in session.query(Permission)
            .filter(Permission.code.in_(perm_codes)).all()
        }

        for code in perm_codes:
            if code not in existing:
                session.add(Permission(code=code, description=code))

        session.flush()

        perms = session.query(Permission).filter(Permission.code.in_(perm_codes)).all()
        admin_role.permissions = perms

        user = session.query(User).filter_by(email=admin_email).first()
        if not user:
            user = User(email=admin_email, name=admin_name, is_active=True, auth_provider="LOCAL")
            user.password_hash = generate_password_hash(
                admin_password, method="pbkdf2:sha256", salt_length=16
            )
            user.role = admin_role
            session.add(user)

        session.commit()

    except Exception:
        session.rollback()
        raise
    finally:
        session.close()