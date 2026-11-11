"""运维脚本包。

这里的脚本改变的是**数据**而不是结构，必须可以重复执行（幂等），
并且默认拒绝触碰 ``data/finance_media.db``。
"""

from __future__ import annotations
