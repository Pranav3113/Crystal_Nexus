from flask import current_app, g
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import click

from ..platform_models import Tenant
from ..cli import seed_all
from .. import db

def register_tenant_seed(app):
    @app.cli.command("seed-tenant")
    @click.option("--slug", required=True, help="Tenant slug (e.g. demo)")
    def seed_tenant(slug):

        # ✅ platform session (never use db.session for platform lookups)
        platform_engine = db.get_engine(current_app, bind="platform")
        PlatformSession = sessionmaker(bind=platform_engine)
        ps = PlatformSession()

        tenant = ps.query(Tenant).filter_by(slug=slug, is_active=True).first()
        if not tenant:
            raise click.ClickException(f"Tenant not found in PLATFORM DB: {slug}")

        # ✅ now point app's tenant engine (your existing pattern)
        engine = create_engine(tenant.db_uri, pool_pre_ping=True, pool_recycle=1800)
        g.tenant_engine = engine

        click.echo(f"Seeding tenant: {slug} -> {tenant.db_uri}")
        seed_all()
        db.session.commit()
        click.echo("Done.")