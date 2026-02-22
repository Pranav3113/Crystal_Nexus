import click
from flask import g, current_app
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .. import db
from ..platform_models import Tenant  # platform bind model(s)

def register_reset_db(app):
    @app.cli.command("reset-db")
    @click.option("--platform-only", is_flag=True, help="Reset only platform DB tables")
    @click.option("--skip-platform", is_flag=True, help="Do not reset platform tables")
    @click.option("--tenant", default=None, help="Reset only this tenant slug's DB tables")
    @click.option("--yes", is_flag=True, help="Skip confirmation")
    def reset_db(platform_only, skip_platform, tenant, yes):
        if not yes:
            if not click.confirm("This will DROP and RECREATE tables. Continue?", default=False):
                click.echo("Cancelled.")
                return

        # ✅ Reset platform DB when requested, or when not skipping and not doing tenant-only
        if platform_only or (not skip_platform and not tenant):
            click.echo("Resetting PLATFORM tables...")
            try:
                g.tenant_engine = None
            except Exception:
                pass

            # Ensure platform models are registered before create_all
            import app.platform_models  # noqa: F401

            db.drop_all(bind="platform")
            db.create_all(bind="platform")
            click.echo("✅ Platform tables recreated.")

        if platform_only:
            return

        if tenant:
            # ✅ CRITICAL: always query Tenant using a session bound to the PLATFORM engine
            platform_engine = db.get_engine(current_app, bind="platform")
            PlatformSession = sessionmaker(bind=platform_engine)
            ps = PlatformSession()

            t = ps.query(Tenant).filter_by(slug=tenant, is_active=True).first()
            if not t:
                raise click.ClickException(f"Tenant not found in PLATFORM DB: {tenant}")

            click.echo(f"Resetting TENANT tables for: {tenant} -> {t.db_uri}")

            # Ensure tenant models are registered before create_all
            import app.models  # noqa: F401

            engine = create_engine(t.db_uri, pool_pre_ping=True, pool_recycle=1800)
            g.tenant_engine = engine

            db.drop_all()    # drops tenant tables (default bind)
            db.create_all()  # creates tenant tables (default bind)

            click.echo("✅ Tenant tables recreated.")
            return

        click.echo("No --tenant provided, tenant DB not reset. (Use --tenant demo)")