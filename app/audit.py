from . import db
from .models import AuditLog
from flask_login import current_user

def log_audit(entity, entity_id, action, field=None, old=None, new=None):
    try:
        log = AuditLog(
            entity=entity,
            entity_id=entity_id,
            action=action,
            field=field,
            old_value=str(old) if old is not None else None,
            new_value=str(new) if new is not None else None,
            performed_by_id=current_user.id if current_user.is_authenticated else None
        )
        db.session.add(log)
        db.session.commit()
    except Exception:
        db.session.rollback()