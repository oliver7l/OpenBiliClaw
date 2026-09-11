"""克隆站点数据目录定位。

K4 sites 迁移后克隆站文件统一存放在数据根目录下的 ``clone-sites/``
（即与数据库文件同级的 ``clone-sites``，例如 ``data/clone-sites``），
本函数负责从数据库对象反推该目录，避免各处硬编码站点根路径。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_DEFAULT_DB = Path("data") / "openbiliclaw.db"


def resolve_clone_sites_dir(database: Any | None = None) -> Path:
    """定位克隆站点数据目录：``<数据库所在目录>/clone-sites``。

    :param database: 提供 ``_db_path`` 属性的数据库对象；为 ``None`` 时回退到
        ``data/openbiliclaw.db`` 同级目录。
    """
    if database is not None:
        db_path = getattr(database, "_db_path", None)
        if db_path is not None:
            return Path(db_path).parent / "clone-sites"
    return _DEFAULT_DB.parent / "clone-sites"
