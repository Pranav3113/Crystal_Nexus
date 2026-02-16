from decimal import Decimal
from app.models import MarginSettings

def get_margin_threshold_percent() -> Decimal:
    ms = (MarginSettings.query
          .filter(MarginSettings.is_active == True)
          .order_by(MarginSettings.id.desc())
          .first())
    return Decimal(str(ms.threshold_percent)) if ms else Decimal("50.00")