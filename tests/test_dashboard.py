# TM - #DASHBOARD - Setup dashboard
"""Tieu chi nghiem thu cua setup dashboard. Chay tren DB tam, khong goi mang.

Nen H1 trong REAL_H1 la so lieu that BTCUSDT 05-07/11/2026: co vu nen 22:00 06/11 (vol 26,303)
va cac setup A/B ngay 07/11 ma yeu cau dung lam vi du.
"""
from __future__ import annotations

import asyncio
import atexit
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="bamcp-dash-")
# Xoa luc thoat tien trinh: neu module nay import server dau tien thi cac module test khac van dung _TMP
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)
# Gan thang, khong setdefault: wipe() xoa bang, tuyet doi khong duoc tro vao du lieu that
if "server" not in sys.modules:
    os.environ["BAMCP_DATA_ROOT"] = _TMP
os.environ.setdefault("BAMCP_ACCOUNT_ENABLED", "false")

import httpx2  # noqa: E402
from argon2 import PasswordHasher  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402

import admin_ui  # noqa: E402
import bamcp_cli  # noqa: E402
import dashboard_auth  # noqa: E402
import dashboard_logic as logic  # noqa: E402
import dashboard_store  # noqa: E402
import entry_logic  # noqa: E402
import kline_store  # noqa: E402
import notifier  # noqa: E402
import server  # noqa: E402
import sqlite_db  # noqa: E402

BASE = "/setup-dashboard"
ADMIN = server.ADMIN_PATH
M5 = 300_000
H1 = 3_600_000
DAY = 86_400_000
OWNER_PASS = "owner-password-1"
VIEWER_PASS = "viewer-password-1"
SECRETS = ("https://discord.com/api/webhooks/123/SECRETHOOK", "bot999:SECRETTOKEN")

REAL_H1_START = "2026-11-05 00:00"
REAL_H1 = """
80494 80535 80188.7 80387.1 5007.592
80387.1 80679.2 80260.2 80314 4750.599
80314 80358 79905.4 79956.8 5779.499
79956.8 80321 79910.1 80099.2 4981.066
80099.2 80280.9 80050.5 80198.6 1879.02
80198.7 80927.5 80185.7 80682.1 6916.718
80682 80699.8 80350 80466.5 4233.726
80466.4 80807.3 80441.7 80509.4 4460.803
80509.4 80888 80214.2 80640.2 5821.145
80640.2 80831.3 80516 80544.2 2510.994
80544.2 80815.4 80495.2 80514.9 2564.295
80515 80706 80337.3 80490.1 3195.401
80490.2 80584.5 80350 80368 2526.749
80368 80679.1 80344 80622.6 2295.132
80622.5 80764.9 80288.7 80362.4 5071.635
80362.4 80629.2 80354.6 80498 3044.262
80497.9 80500 80260.2 80443.2 3421.236
80443.1 80519.1 80208.3 80277.4 3605.63
80277.4 80420 80223.7 80389.8 2662.78
80389.7 80389.8 79485.2 79659.4 13747.936
79659.3 79856.1 79487.6 79646.4 7805.789
79646.4 79712 78011 78262 28882.72
78262.1 78266 75525 76007.4 77910.519
76007.6 76976.7 75637.5 76361 33301.893
76361 76377.6 75788 75914.5 12651.024
75914.4 76248.4 75669.4 76067.2 8821.446
76067.1 76532.5 75853.5 76336.5 7518.694
76336.6 76382 75922.3 76200.8 6633.553
76200.8 76200.9 75726.3 75825.6 7114.748
75825.6 76099.4 75561.7 75685 6745.579
75685 76079.9 75158 76050.1 15233.81
76050 76381.4 76020.1 76300 7021.86
76300 76364.6 75860.8 76099 4698.886
76098.9 76268 75955.2 76162.5 4002.189
76162.5 76247 75731 75880.7 4556.657
75880.6 75886 75588 75742.1 3978.563
75742.1 75787.7 75327.7 75669.5 5509.567
75669.4 75764.6 75423.4 75670 3579.538
75670.1 75800 75260.4 75572.8 8876.962
75572.9 75785.4 75446 75640.2 4140.012
75640.2 76253.6 75629.9 76148.8 7360.064
76148.7 76272.5 76033.4 76033.4 2709.356
76033.4 76227.2 75925 76117.3 3071.264
76117.2 76350.9 75978 76005.5 4286.594
76005.5 76085.6 75560.4 75863.6 12466.478
75863.7 76123.3 75270.5 75517.2 18469.717
75517.3 75858.8 74810.8 75832.8 26302.507
75832.8 76045.9 75376 75978.4 9238.22
75978.5 76126 75472.8 75750 6387.454
75750.1 75972.5 75633 75764.8 2370.335
75764.9 75866.1 75644 75863.8 1974.844
75863.8 75986.7 75720 75860 3930.284
75860 75900.8 75689 75889.8 1746.391
75889.8 76182.5 75863.3 75871.6 2935.717
75871.7 75960 75777.8 75810.1 1971.344
75810 75943.5 75805 75867 1196.375
75867.1 76000 75819.9 76000 1118.103
76000 76067.5 75976 76008 1270.713
76008 76068.9 75742.3 75864 2974.388
75864 75888 75803.8 75864.3 960.589
75864.3 76014.1 75851 75994.9 1373.415
75994.9 76030.8 75973.2 76011.2 959.315
76011.2 76087.9 75962 76053.5 1182.791
76053.6 76161.1 76050.2 76161 1422.695
"""


# TM - #WYCKOFF-TR - Wyckoff phases and events on locked TR
# Nen M15 that BTCUSDT 05/11 16:00 -> 08/11 05:45 (o h l c v), lay tu get_klines ngay 08/11. Du 20 nen
# truoc SC 22:45 05/11 de tinh SMA20, va co du SC, AR, ST, SPRING, TEST x 2 cua TR b9a9d6b7.
REAL_M15_START = "2026-11-05 16:00"
REAL_M15 = """
80497.9 80500 80330 80330 984.83
80330 80389.8 80303.6 80315 769.232
80314.9 80391.9 80260.2 80298.1 1090.85
80298.1 80450 80297.7 80443.2 576.324
80443.1 80450 80358 80374.4 634.858
80374.3 80517.7 80374.3 80486.1 458.605
80486.1 80519.1 80321.1 80321.1 1298.916
80321.2 80332.7 80208.3 80277.4 1213.251
80277.4 80323.4 80223.7 80320 521.703
80319.9 80420 80250 80268.3 1260.006
80268.2 80360.8 80268.2 80360.7 392.24
80360.7 80400 80339.3 80389.8 488.831
80389.7 80389.8 80143.2 80249.4 1345.741
80249.4 80249.5 80062.5 80080 1281.739
80080.1 80099.9 79640.5 79700.1 5864.873
79700 79800 79485.2 79659.4 5255.583
79659.3 79747.3 79487.6 79684.5 2745.739
79684.6 79732.2 79568.8 79662.5 1295.012
79662.5 79755.4 79577.5 79734.9 1152.413
79735 79856.1 79562 79646.4 2612.625
79646.4 79712 79311 79381.1 3949.811
79381.1 79503.9 79304 79477 2155.69
79477 79605.9 78829.7 78846.5 9754.021
78846.5 78960 78011 78262 13023.198
78262.1 78266 76800 76990.7 24334.855
76990.7 77638.3 76135.7 76398 25992.927
76398 76691.4 76214 76381.2 9386.313
76381.2 76400.2 75525 76007.4 18196.424
76007.6 76318.3 75637.5 76312.6 11999.861
76312.6 76976.7 76172.1 76827 11865.714
76827.1 76957.8 76404.4 76423.2 6345.932
76423.2 76694.5 76348.9 76361 3090.386
76361 76377.6 76053.3 76172.1 4275.48
76172.1 76216 75803.3 75897.6 3700.067
75897.6 76129 75788 75959.3 2702.895
75959.3 76043.9 75800 75914.5 1972.582
75914.4 76207.2 75669.4 76103.5 3794.403
76103.4 76236.6 75868.8 76136.1 2480.208
76136.1 76248.4 75950 76172.2 1387.041
76172.1 76245 75979 76067.2 1159.794
76067.1 76223.1 75853.5 76218.3 1674.717
76218.2 76402.4 75998.6 76006.2 2325.911
76006.1 76453.9 75987.7 76397.7 1861.921
76397.8 76532.5 76300.2 76336.5 1656.145
76336.6 76336.7 75984.9 76101.8 1893.012
76101.8 76134.1 75929.5 76065.3 1665.597
76065.3 76253.6 75922.3 76181 1769.444
76181 76382 76087.7 76200.8 1305.5
76200.8 76200.9 75871.2 76030.5 2378.431
76030.5 76053 75726.3 75975.3 2098.659
75975.3 76121.5 75902 75913.7 1331
75913.8 76005.2 75735.6 75825.6 1306.658
75825.6 76096.1 75733.3 76081.6 1714.52
76081.6 76099.4 75895.9 75974.2 816.437
75974.1 75998.3 75749 75816.4 1876.141
75816.5 75816.5 75561.7 75685 2338.481
75685 75770.4 75158 75369.2 7741.78
75369.1 75972.8 75267.8 75838 3615.082
75837.9 76053.3 75768.3 76053.2 2110.113
76053.3 76079.9 75846.7 76050.1 1766.835
76050 76292 76020.1 76098.3 2741.594
76098.3 76255.3 76052.6 76152.5 1160.471
76152.5 76381.4 76130.7 76376.2 2149.466
76376.2 76376.3 76159.6 76300 970.329
76300 76364.6 76105 76285.4 1168.47
76285.3 76285.4 75860.8 76038 1868.649
76038.1 76246.6 75961 76088.6 800.546
76088.6 76234.6 76064 76099 861.221
76098.9 76186.7 75980.2 76107.7 1028.702
76107.7 76264.4 75998.1 76155.4 1259.548
76155.4 76173.9 75955.2 76090 908.401
76090.1 76268 76079 76162.5 805.538
76162.5 76247 76024.9 76109.3 712.901
76109.2 76109.3 75910.1 75938.3 1278.889
75938.1 75960 75731 75864.9 1820.342
75864.8 75954.7 75744.8 75880.7 744.525
75880.6 75886 75650 75705.5 1233.18
75705.5 75705.6 75588.9 75630.8 1012.97
75630.8 75826.4 75588 75732.2 1010.947
75732.2 75789 75685.9 75742.1 721.466
75742.1 75787.7 75542.8 75542.9 1082.116
75542.9 75743.2 75428.5 75478 1557.073
75477.9 75700 75327.7 75590.7 1841.204
75590.8 75742 75590.8 75669.5 1029.174
75669.4 75669.4 75423.4 75441.1 1383.601
75441.2 75650 75441.1 75636.7 766.574
75636.7 75764.6 75534 75696.9 841.729
75696.9 75743.1 75623.9 75670 587.634
75670.1 75679.1 75355 75355 1715.006
75355.1 75457.4 75300.5 75325.7 1269.221
75325.8 75757.3 75260.4 75678.4 3698.022
75678.5 75800 75552.7 75572.8 2194.713
75572.9 75640 75446 75633.5 1315.924
75633.5 75785.4 75597.3 75616.9 1670.36
75616.8 75697.8 75501.1 75676.9 724.887
75676.8 75727.3 75628.7 75640.2 428.841
75640.2 76113.3 75629.9 76092.3 3328.133
76092.3 76253.6 76072.1 76122.8 1967.223
76122.8 76242.4 76057.4 76089.9 1146.925
76089.8 76160 76020 76148.8 917.783
76148.7 76192.8 76070 76145.1 688.847
76145 76206.9 76044.5 76139 561.928
76139 76272.5 76111.5 76191.9 834.797
76191.8 76203.8 76033.4 76033.4 623.784
76033.4 76085 75925 75966.2 1152.414
75966.1 76101.5 75950.6 76084.2 695.647
76084.1 76184.5 76068 76136.8 669.609
76136.9 76227.2 76072.1 76117.3 553.594
76117.2 76184.1 76031.7 76099.2 644.476
76099.2 76350.9 76091.8 76300 1633.952
76299.9 76313.4 76095.4 76188.3 784.481
76188.2 76206.9 75978 76005.5 1223.685
76005.5 76077.3 75783.9 75877.2 3130.894
75877.1 76039.1 75850.1 76011.4 1526.879
76011.4 76085.6 75560.4 75776.3 6695.231
75776.3 75960 75751.2 75863.6 1113.474
75863.7 75913.2 75439 75566.9 3591.657
75566.9 75725.7 75524.2 75602.5 1459.007
75602.6 76000 75270.5 75715.2 7027.962
75715.3 76123.3 75295.8 75517.2 6391.091
75517.3 75822 74810.8 75814 16180.055
75813.9 75814 75546.4 75614.9 4592.577
75614.8 75717.5 75258 75696.2 3254.801
75696.3 75858.8 75589 75832.8 2275.074
75832.8 75848 75425 75505 2024.08
75505.1 75812.1 75376 75753.6 2264.234
75753.6 75927.8 75596 75623.2 2385.306
75623 76045.9 75604 75978.4 2564.6
75978.5 76126 75830.7 75927.3 1991.471
75927.4 76001.9 75807.4 75854.9 1283.878
75854.9 75919.6 75506.1 75629.6 1833.141
75629.5 75780 75472.8 75750 1278.964
75750.1 75801.5 75633 75797.3 648.139
75797.2 75832 75682 75828.4 427.554
75828.5 75972.5 75782.2 75853.4 797.541
75853.3 75881.5 75740 75764.8 497.101
75764.9 75811.8 75713 75754.4 443.673
75754.4 75770 75644 75674.8 748.303
75674.8 75800 75666.5 75770.7 351.333
75770.7 75866.1 75706.3 75863.8 431.535
75863.8 75986.7 75821 75821 530.06
75821 75849.4 75720 75790.8 428.16
75790.7 75900 75762.5 75873.5 558.141
75873.4 75900 75800 75860 2413.923
75860 75883.6 75810 75873.6 763.008
75873.6 75886.7 75746 75764 356.153
75764 75794 75689 75794 274.762
75793.9 75900.8 75780 75889.8 352.468
75889.8 76182.5 75889.8 75979.6 1628.757
75979.6 76051.3 75968.6 76022.4 395.632
76022.3 76034 75960 76016.3 446.793
76016.3 76016.4 75863.3 75871.6 464.535
75871.7 75908.7 75854 75861.4 392.489
75861.4 75921.9 75859.7 75898.7 268.678
75898.7 75960 75898.6 75920.8 327.405
75920.7 75953.9 75777.8 75810.1 982.772
75810 75928 75805 75927.8 529.202
75927.7 75943.5 75894.1 75901.1 275.33
75901 75916 75877.6 75884.1 164.788
75884.1 75895.4 75857 75867 227.055
75867.1 75965.1 75867 75869 356.722
75869 75888 75819.9 75867 234.812
75867.1 75984.5 75866 75973.7 269.523
75973.8 76000 75950 76000 257.046
76000 76067.5 75999.9 76066.1 343.775
76066.2 76066.2 76013.9 76024.8 325.911
76024.7 76036.8 75990 76013.8 373.705
76013.8 76064 75976 76008 227.322
76008 76068.9 76008 76041.8 207.053
76041.8 76041.9 75962 75967 213.048
75967 75986.6 75742.3 75762 2094.293
75762 75864 75757.3 75864 459.994
75864 75865.8 75822.3 75822.4 260.996
75822.4 75854.2 75803.8 75820.9 302.722
75820.9 75873.2 75818.6 75848.3 168.927
75848.3 75888 75811.4 75864.3 227.944
75864.3 75893 75851 75879.7 296.722
75879.7 76014.1 75879.7 75985.4 462.653
75985.4 76011 75960 75968.3 389.003
75968.3 76008 75968.2 75994.9 225.037
75994.9 76011 75973.2 76007.6 345.635
76007.5 76030.8 75987.2 76026.4 365.694
76026.3 76030 75994 76009.4 134.896
76009.4 76011.3 75994.6 76011.2 113.09
76011.2 76038 75968.5 75978.9 208.251
75979 75994.9 75962 75980.3 190.576
75980.3 75999.5 75968 75999.5 222.198
75999.5 76087.9 75999.4 76053.5 561.766
76053.6 76099.5 76050.2 76076 469.23
76076 76107.2 76076 76080.3 299.108
76080.3 76098.9 76079 76098.9 151.036
76098.9 76161.1 76098.8 76161 503.321
76161.1 76210.4 76111.3 76159.6 825.066
76159.5 76159.6 76031.3 76037.6 719.706
76037.5 76094 76030.1 76052.1 395.483
76052.1 76060.3 75980.9 76015.5 315.909
76015.4 76043.8 75996 76003.9 161.728
76003.9 76012.2 75913.8 75928.8 516.635
75928.8 75965 75909 75943.2 377.91
75943.2 75962 75922.5 75961.9 138.704
75962 76002.5 75931.7 75942.6 210.324
75942.6 75949.7 75890 75934.5 230.61
75934.4 75991.4 75934.4 75981.6 243.557
75981.6 75993.4 75945.1 75945.2 189.971
75945.2 75981.2 75940.2 75969.8 168.61
75969.8 75974 75969.6 75974 59.82
75974 75974 75786.8 75807 1576.258
75807.1 75865.8 75790.1 75860.8 446.059
75860.8 75878.8 75791.1 75805.4 376.394
75805.3 75863.8 75800 75863.8 302.157
75863.8 75905 75863.7 75904.9 212.132
75905 75950.3 75898.1 75903.1 321.724
75903 75919 75898.1 75913 186.638
75912.9 75913 75895 75900.1 174.986
75900 75946.9 75900 75921 270.148
75921.1 75950.1 75905 75916.3 249.87
75916.4 75916.4 75832 75835.1 274.646
75835.1 75854 75805.4 75825.7 280.904
75825.6 75825.7 75730 75778.3 637.472
75778.2 75778.3 75562 75728 1813.298
75728 75750 75702.3 75746.8 355.095
75746.8 75770.8 75660 75666.1 331.201
75666.1 75756.4 75666.1 75741.3 236.196
75741.4 75755.1 75683.5 75712.2 214.163
75712.2 75784.9 75712.2 75782.1 219.253
75782.1 75790 75749.5 75763.7 130.948
75763.7 75845.5 75763.6 75824.1 290.696
75824.1 75826.8 75809.3 75809.4 137.843
75809.3 75839.2 75804.3 75839.1 177.12
75839.1 75860.1 75771.7 75774 308.203
75774 75802 75771.7 75801.9 160.823
75802 75805.4 75765.2 75765.3 211.839
75765.3 75798.6 75762 75762.1 164.23
75762.1 75781.1 75742.1 75766.4 196.039
75766.3 75770.1 75720.1 75723.1 287.248
75723.1 75772.5 75718.9 75772.5 110.284
75772.5 75776 75745 75770.1 154.2
75770.1 75771.9 75722 75724.1 166.443
75724.1 75724.2 75692 75694.1 247.581
75694 75694.1 75571.6 75657.7 1063.49
75657.6 75677.3 75575.9 75586.1 477.48
75586.1 75616 75575.9 75603 227.814
75602.9 75671.1 75526.9 75671.1 636.085
75671.1 75715.9 75648 75715.8 192.813
75715.9 75748 75705.1 75705.1 256.764
75705.1 75779.2 75688.3 75691 417.34
75691 75779.1 75688.3 75765.9 375.74
75765.9 75765.9 75704 75749.1 121.475
"""
M15 = 900_000


def vn_ms(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=server.TZ).timestamp() * 1000)


def real_h1_rows() -> list[list]:
    start = vn_ms(REAL_H1_START)
    rows = []
    for index, line in enumerate(REAL_H1.strip().splitlines()):
        o, h, low, c, v = (float(x) for x in line.split())
        rows.append([start + index * H1, o, h, low, c, v, start + (index + 1) * H1 - 1])
    return rows


# TM - #WYCKOFF-TR - Wyckoff phases and events on locked TR
def real_m15_rows(until: str | None = None) -> list[list]:
    """Nen M15 that; until = chi lay nen mo truoc moc nay (gio VN)."""
    start = vn_ms(REAL_M15_START)
    stop = vn_ms(until) if until else None
    rows = []
    for index, line in enumerate(REAL_M15.strip().splitlines()):
        o, h, low, c, v = (float(x) for x in line.split())
        t = start + index * M15
        if stop is None or t < stop:
            rows.append([t, o, h, low, c, v, t + M15 - 1])
    return rows


def flat_rows(span: int, count: int, *, price: float = 80000.0, volume: float = 10.0,
              end: int | None = None) -> list[list]:
    """`count` nen da dong lien tiep, ket thuc o nen vua dong truoc `end`."""
    end = end if end is not None else int(time.time() * 1000)
    last = end // span * span - span
    return [[last - (count - 1 - i) * span, price, price + 5, price - 5, price, volume,
             last - (count - 1 - i) * span + span - 1] for i in range(count)]


SETUP_A = {"direction": "long", "zone_low": 75400, "zone_high": 75560, "trigger_type": "rejection",
           "trigger_params": {"max_vol_mult": 0.8}, "entry": 75480, "stop": 75090, "target": 76330,
           "invalidate_level": 75158, "tr_high": 76976.7, "tr_low": 75525, "note": "A long test",
           "branch": "accumulation", "entry_event": "TEST"}  # TM - #TR-BRANCH - truong bat buoc
SETUP_B = {"direction": "short", "zone_low": 77320, "zone_high": 77600, "trigger_type": "rejection",
           "trigger_params": {"max_vol_mult": 0.8}, "entry": 77420, "stop": 77760, "target": 76560,
           "invalidate_level": 77700, "note": "B short", "branch": "range", "entry_event": "UT"}
SETUP_B_0511 = {"direction": "long", "zone_low": 81400, "zone_high": 81600, "trigger_type": "close_above",
                "entry": 81500, "stop": 81200, "target": 83000, "invalidate_level": 81350, "m15_filter": False,
                "branch": "accumulation", "entry_event": "SOS"}
REAL_TR = {"timeframe": "15m", "tr_high": 76976.7, "tr_low": 75525, "since": "2026-11-05 22:45",
           "events": [{"type": "SC", "t": "2026-11-05 22:45", "price": 75525},
                      {"type": "AR", "t": "2026-11-05 23:15", "price": 76976.7},
                      {"type": "ST", "t": "2026-11-06 06:00", "price": 75158},
                      {"type": "Spring", "t": "2026-11-06 22:00", "price": 74810.8}],
           "redraw_rule": "M15 dong < 75158 hoac H1 dong > 76976.7"}


def scenario(**kw) -> dict:
    base = {"kind": "main", "label": "KB chính A: test LPS rồi lên creek",
            "points": [{"t": vn_ms("2026-11-07 16:00"), "price": 75500},
                       {"t": vn_ms("2026-11-07 20:00"), "price": 76330}],
            "condition": "M15 dong < 75560 voi vol thap"}
    return {**base, **kw}


def run(coro):
    return asyncio.run(coro)


def wipe() -> None:
    root = Path(tempfile.gettempdir()).resolve()
    if root not in Path(server.SQLITE_PATH).resolve().parents:
        raise RuntimeError(f"test chi duoc chay tren thu muc tam, dang tro vao {server.SQLITE_PATH}")
    with sqlite_db.connect(server.SQLITE_PATH) as conn:
        for table in ("tr_events", "tr_phases", "tr_event_deletions",  # TM - #WYCKOFF-TR
                      "tr_prob_history",  # TM - #TR-BRANCH
                      "scenarios", "trading_ranges", "plan_snapshots", "setup_reviews", "setups", "setup_events",
                      "dashboard_users", "dashboard_sessions", "dashboard_audit", "dashboard_login_log",
                      "dashboard_login_guard", "admin_sessions"):
            conn.execute(f"DELETE FROM {table}")
    server._DASH_CHART_CACHE.clear()
    server._DASH_EXCHANGE_CACHE.clear()
    server.DASH_FILE.unlink(missing_ok=True)
    server._DASH_CFG_CACHE.update(key=None, cfg=None)
    shutil.rmtree(server.BIAS_DIR, ignore_errors=True)


class Base(unittest.TestCase):
    def setUp(self):
        wipe()
        self.symbol = server.DEFAULT_SYMBOL
        self.klines_path = Path(tempfile.mkdtemp(prefix="klines-", dir=_TMP)) / "k.db"
        self.klines = kline_store.SqliteKlineStore(self.klines_path, span_ms=server._tf_ms)
        patcher = mock.patch.object(server, "KLINE_STORE", self.klines)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(wipe)

    def save_setups(self, *setups, day="2026-11-07", replace=False):
        expires = int(time.time() * 1000) + DAY
        return server.save_setups(self.symbol, [{"bias_date": day, "expires_at": expires, **s} for s in setups],
                                  replace)

    def plan_0711(self):
        self.klines.upsert(self.symbol, "1h", real_h1_rows())
        # TM - #WYCKOFF-TR: event TR phai nam dung nen M15 da dong; cat o 16:00 07/11 cho khop nen H1
        self.klines.upsert(self.symbol, "15m", real_m15_rows("2026-11-07 16:00"))
        server.save_bias("neutral", "Đứng ngoài chờ biên TR", symbol=self.symbol, phase="C",
                         key_levels={"creek_low": 76350.9}, action_zone="A/B", date="2026-11-07")
        saved = self.save_setups(SETUP_A, SETUP_B)
        self.assertEqual(saved["rejected"], [])
        codes = saved["codes"]
        out = server.save_scenarios(self.symbol, [
            scenario(setup_codes=[codes[0]]),
            scenario(kind="alt", label="KB phụ B", setup_codes=[codes[1]], points=[
                {"t": vn_ms("2026-11-07 16:00"), "price": 76200},
                {"t": vn_ms("2026-11-07 21:00"), "price": 77400}])], bias_date="2026-11-07", tr=REAL_TR)
        self.assertEqual(out["rejected"], [])
        return codes, out


class InvalidateLevelTest(Base):
    """Muc 4.3: setup B ngay 05/11 bi huy ngay khi tao."""

    def price(self, value):
        self.klines.upsert(self.symbol, "5m", flat_rows(M5, 3, price=value))

    def test_long_rejected_like_setup_b_0511(self):
        self.price(80536.9)
        out = self.save_setups(SETUP_B_0511)
        self.assertEqual(out["saved"], [])
        self.assertEqual(out["rejected"][0]["error"],
                         "invalidate_level 81350 da bi pha tai gia hien tai 80536.9 - setup se huy ngay")

    def test_short_mirror_and_boundary(self):
        self.price(77700)
        short = {**SETUP_B, "zone_low": 77320, "invalidate_level": 77700}
        self.assertIn("da bi pha", self.save_setups(short)["rejected"][0]["error"])
        self.price(77699.9)
        self.assertEqual(self.save_setups(short)["rejected"], [])

    def test_confirm_setup_saved_with_flag(self):
        self.price(80536.9)
        out = self.save_setups({**SETUP_B_0511, "confirm_type": "close_above", "confirm_level": 81450})
        self.assertEqual(out["rejected"], [])
        row = server.SETUPS.get(out["codes"][0])
        self.assertTrue(row["invalidate_after_confirm"])
        self.assertTrue(server.get_setups(self.symbol, date="2026-11-07")["setups"][0]["invalidate_after_confirm"])

    def test_explicit_false_keeps_check(self):
        self.price(80536.9)
        out = self.save_setups({**SETUP_B_0511, "confirm_type": "close_above", "confirm_level": 81450,
                                "invalidate_after_confirm": False})
        self.assertIn("da bi pha", out["rejected"][0]["error"])

    def test_flag_needs_confirm(self):
        out = self.save_setups({**SETUP_B_0511, "invalidate_after_confirm": True})
        self.assertIn("confirm_type", out["rejected"][0]["error"])

    def test_no_price_does_not_block(self):
        out = self.save_setups(SETUP_B_0511)
        self.assertEqual(out["rejected"], [])
        self.assertTrue(out["warnings"])

    def step_setup(self, **kw):
        base = {"id": "s", "code": "BTC-X-A", "symbol": "BTCUSDT", "status": "WAITING", "state": {},
                "direction": "long", "zone_low": 81400.0, "zone_high": 81600.0, "trigger_type": "close_above",
                "trigger_params": {}, "confirm_type": "close_above", "confirm_level": 81450.0, "entry": 81500.0,
                "stop": 81200.0, "target": 83000.0, "max_chase": None, "invalidate_level": 81350.0,
                "m15_filter": False, "expires_at": 10 ** 15, "tr_since": None, "created_at": 0,
                "invalidate_after_confirm": True}
        return {**base, **kw}

    def walk(self, setup, closes):
        statuses = []
        for index, close in enumerate(closes):
            bar = {"open_time": index * M5, "close_time": index * M5 + M5 - 1, "open": close, "high": close,
                   "low": close, "close": close, "volume": 10.0}
            ctx = {"params": dict(server.ALERT_PARAM_DEFAULTS), "history": [], "m15_close": None, "min_rr": 1.5,
                   "day": "2026-11-05", "check": lambda p: {"would_pass": True, "rule_violations": []}}
            status, state, _ = entry_logic.step(setup, bar, ctx)
            setup = {**setup, "status": status, "state": state}
            statuses.append(status)
        return setup, statuses

    def test_not_cancelled_until_confirm_reached(self):
        setup, statuses = self.walk(self.step_setup(), [80536.9, 80900, 81000])
        self.assertNotIn("INVALIDATED", statuses, "chua dat confirm thi chua xet invalidate")
        self.assertFalse(setup["state"].get("confirm_reached"))
        setup, statuses = self.walk(setup, [81460, 81300])
        self.assertTrue(setup["state"]["confirm_reached"])
        self.assertEqual(statuses[-1], "INVALIDATED", "da confirm roi thi dong duoi muc la huy")

    def test_existing_setups_keep_old_behaviour(self):
        _, statuses = self.walk(self.step_setup(invalidate_after_confirm=False), [80536.9])
        self.assertEqual(statuses, ["INVALIDATED"])


class ScenarioTest(Base):
    def test_save_returns_codes_and_link(self):
        codes, out = self.plan_0711()
        self.assertEqual(out["codes"], ["BTC-20261107-S1", "BTC-20261107-S2"])
        self.assertTrue(out["dashboard_url"].startswith("https://bamcp.online/setup-dashboard?"))
        self.assertIn("symbol=BTCUSDT", out["dashboard_url"])
        self.assertIn("date=2026-11-07", out["dashboard_url"])

    def test_validation(self):
        self.klines.upsert(self.symbol, "1h", real_h1_rows())
        out = server.save_scenarios(self.symbol, [
            scenario(points=[{"t": 1, "price": 1}]),
            scenario(setup_codes=["BTC-20261107-Z"]),
            scenario(kind="other"),
            scenario(label=""),
            scenario(points=[{"t": vn_ms("2026-11-07 20:00"), "price": 75000}, {"t": vn_ms("2026-11-07 16:00"), "price": 75100}]),
        ], bias_date="2026-11-07")
        self.assertEqual([r["index"] for r in out["rejected"]], [0, 1, 2, 3, 4])
        self.assertEqual(out["codes"], [])
        self.assertIn("setup_codes khong ton tai", out["rejected"][1]["error"])

    def test_far_first_point_only_warns(self):
        self.klines.upsert(self.symbol, "1h", real_h1_rows())
        out = server.save_scenarios(self.symbol, [scenario(points=[
            {"t": vn_ms("2026-11-07 16:00"), "price": 70000}, {"t": vn_ms("2026-11-07 20:00"), "price": 76000}])],
            bias_date="2026-11-07")
        self.assertEqual(len(out["codes"]), 1)
        self.assertEqual(len(out["warnings"]), 1)
        self.assertIn("76161", out["warnings"][0])

    def test_offset_points_resolve_from_last_closed_bar(self):
        rows = real_h1_rows()
        self.klines.upsert(self.symbol, "1h", rows)
        out = server.save_scenarios(self.symbol, [scenario(points=[
            {"t": {"bars": 1, "tf": "1h"}, "price": 76200},
            {"t": {"bars": 4, "tf": "1h"}, "price": 76400, "note": "creek"}])], bias_date="2026-11-07")
        points = server.PLANS.scenario(out["codes"][0])["points"]
        self.assertEqual(points[0]["t"], rows[-1][0] + H1)
        self.assertEqual(points[1]["t"], rows[-1][0] + 4 * H1)
        self.assertEqual(points[1]["offset"], {"bars": 4, "tf": "1h"})
        self.assertEqual(points[1]["note"], "creek")

    def test_replace_cancels_active(self):
        self.klines.upsert(self.symbol, "1h", real_h1_rows())
        first = server.save_scenarios(self.symbol, [scenario()], bias_date="2026-11-07")
        second = server.save_scenarios(self.symbol, [scenario(label="moi")], bias_date="2026-11-07")
        self.assertEqual(second["cancelled"], first["codes"])
        self.assertEqual(server.PLANS.scenario(first["codes"][0])["status"], "cancelled")
        self.assertEqual(second["codes"], ["BTC-20261107-S2"])
        kept = server.save_scenarios(self.symbol, [scenario(label="them")], replace=False, bias_date="2026-11-07")
        self.assertEqual(kept["cancelled"], [])

    def test_rejected_all_does_not_cancel(self):
        self.klines.upsert(self.symbol, "1h", real_h1_rows())
        first = server.save_scenarios(self.symbol, [scenario()], bias_date="2026-11-07")
        out = server.save_scenarios(self.symbol, [scenario(label="")], bias_date="2026-11-07")
        self.assertEqual(out["cancelled"], [])
        self.assertEqual(server.PLANS.scenario(first["codes"][0])["status"], "active")

    def test_status_update_by_code(self):
        self.klines.upsert(self.symbol, "1h", real_h1_rows())
        first = server.save_scenarios(self.symbol, [scenario()], bias_date="2026-11-07")
        out = server.save_scenarios(self.symbol, [{"code": first["codes"][0], "status": "played_out"}],
                                    bias_date="2026-11-07")
        self.assertEqual(out["updated"], [{"code": first["codes"][0], "status": "played_out"}])
        self.assertEqual(out["cancelled"], [], "doi trang thai khong duoc huy kich ban khac")

    def test_tr_superseded_only_when_different(self):
        self.klines.upsert(self.symbol, "1h", real_h1_rows())
        self.klines.upsert(self.symbol, "15m", real_m15_rows("2026-11-07 16:00"))  # TM - #WYCKOFF-TR
        first = server.save_scenarios(self.symbol, [scenario()], bias_date="2026-11-07", tr=REAL_TR)["tr"]
        self.assertEqual([e["type"] for e in first["events"]], ["SC", "AR", "ST", "SPRING"])
        same = server.save_scenarios(self.symbol, [], replace=False, bias_date="2026-11-07", tr=REAL_TR)
        self.assertEqual(same["tr"]["id"], first["id"])
        self.assertIsNone(same["plan_revision"], "khong doi gi thi khong sinh snapshot")
        new = server.save_scenarios(self.symbol, [], replace=False, bias_date="2026-11-07",
                                    tr={**REAL_TR, "tr_high": 77000})["tr"]
        self.assertNotEqual(new["id"], first["id"])
        with sqlite_db.connect(server.SQLITE_PATH) as conn:
            old = conn.execute("SELECT superseded_by FROM trading_ranges WHERE id = ?", (first["id"],)).fetchone()
        self.assertEqual(old[0], new["id"])


class SnapshotTest(Base):
    def test_every_save_makes_a_revision(self):
        codes, _ = self.plan_0711()
        rows = server.PLANS.snapshots(self.symbol, "2026-11-07")
        self.assertEqual([(r["revision"], r["trigger"]) for r in rows],
                         [(1, "save_bias"), (2, "save_setups"), (3, "save_scenarios")])
        payload = server.PLANS.snapshot(self.symbol, "2026-11-07", 3)["payload"]
        self.assertEqual(set(payload), {"bias", "setups", "scenarios", "tr", "last_closed_bar"})
        self.assertEqual(payload["last_closed_bar"]["1h"]["c"], 76161)
        cancelled = server.cancel_setup(codes[1], "het hieu luc")
        self.assertEqual(cancelled["plan_revision"], 4)
        self.assertIn("dashboard_url", cancelled)
        self.assertEqual(server.PLANS.snapshots(self.symbol, "2026-11-07")[-1]["trigger"], "cancel_setup")

    def test_review_shows_original_after_revise(self):
        self.plan_0711()
        server.save_bias("short", "Đổi ý", symbol=self.symbol, date="2026-11-07")
        original = run(server.get_chart_data(self.symbol, date="2026-11-07", revision=1))
        self.assertEqual(original["bias"]["direction"], "neutral")
        self.assertEqual(original["plan"]["source"], "revision")
        self.assertEqual(len(original["candles"]), 64, "nen van la that")
        latest = run(server.get_chart_data(self.symbol, date="2026-11-07", revision=4))
        self.assertEqual(latest["bias"]["direction"], "short")
        live = run(server.get_chart_data(self.symbol, date="2026-11-07"))
        self.assertEqual(live["bias"]["direction"], "short")
        with self.assertRaisesRegex(ValueError, "khong co revision 9"):
            run(server.get_chart_data(self.symbol, date="2026-11-07", revision=9))

    def test_origin_combines_first_version_of_each_part(self):
        """R1 chi co bias vi save_bias chay truoc; ke hoach goc phai du setup va kich ban."""
        codes, _ = self.plan_0711()
        server.save_bias("short", "Đổi ý", symbol=self.symbol, date="2026-11-07")
        server.cancel_setup(codes[0], "doi y")
        self.assertEqual(run(server.get_chart_data(self.symbol, date="2026-11-07", revision=1))["setups"], [])
        origin = run(server.get_chart_data(self.symbol, date="2026-11-07", revision="origin"))
        self.assertEqual(origin["plan"]["source"], "origin")
        self.assertEqual(origin["plan"]["revisions_used"], [1, 2, 3])
        self.assertEqual(origin["bias"]["direction"], "neutral")
        self.assertEqual([s["status"] for s in origin["setups"]], ["WAITING", "WAITING"])
        self.assertEqual(len(origin["scenarios"]), 2)
        self.assertEqual(origin["tr"]["tr_low"], 75525)

    def test_store_has_no_update_or_delete_for_snapshots(self):
        names = [n for n in dir(dashboard_store.PlanStore) if "snapshot" in n]
        self.assertEqual(sorted(names), ["add_snapshot", "snapshot", "snapshots"])


class ChartDataTest(Base):
    def test_one_call_returns_everything_for_0711(self):
        codes, _ = self.plan_0711()
        out = run(server.get_chart_data(self.symbol, "1h", 48, date="2026-11-07"))
        self.assertEqual(len(out["candles"]), 48)
        self.assertEqual(len(out["candles"][0]), 7)
        self.assertTrue(all(c[6] is True for c in out["candles"]))
        self.assertEqual(out["bias"]["direction"], "neutral")
        self.assertEqual(out["bias"]["phase"], "C")
        self.assertEqual([s["code"] for s in out["setups"]], codes)
        a, b = out["setups"]
        self.assertEqual((a["zone_low"], a["zone_high"]), (75400, 75560))
        self.assertEqual((b["zone_low"], b["zone_high"]), (77320, 77600))
        self.assertEqual(a["status"], "WAITING")
        self.assertEqual(a["last_event"]["event"], "CREATED")
        self.assertAlmostEqual(a["rr"], 2.18, places=2)
        self.assertEqual(len(out["scenarios"]), 2)
        self.assertEqual((out["tr"]["tr_low"], out["tr"]["tr_high"]), (75525, 76976.7))
        self.assertEqual(out["positions"]["enabled"], False)
        self.assertIn("tf=1h", out["dashboard_url"])
        self.assertEqual(len(out["vol_avg"]), 48)

    def test_vol_spike_on_real_h1_2200_0611(self):
        self.klines.upsert(self.symbol, "1h", real_h1_rows())
        out = run(server.get_chart_data(self.symbol, "1h", 48, date="2026-11-07"))
        target = vn_ms("2026-11-06 22:00")
        self.assertIn(target, out["vol_spike"])
        index = [c[0] for c in out["candles"]].index(target)
        self.assertEqual(round(out["candles"][index][5]), 26303)
        self.assertAlmostEqual(out["candles"][index][5] / out["vol_avg"][index], 3.81, places=2)
        self.assertNotIn(vn_ms("2026-11-07 07:00"), out["vol_spike"])

    def test_vol_spike_uses_config_multiplier(self):
        self.klines.upsert(self.symbol, "1h", real_h1_rows())
        target = vn_ms("2026-11-06 22:00")
        server._write_json(server.DASH_FILE, {"vol_spike_mult": 5.0})
        server._DASH_CFG_CACHE.update(key=None, cfg=None)
        self.assertNotIn(target, run(server.get_chart_data(self.symbol, "1h", 48, date="2026-11-07"))["vol_spike"])

    def test_past_date_ends_24h_after_day(self):
        self.klines.upsert(self.symbol, "1h", real_h1_rows())
        out = run(server.get_chart_data(self.symbol, "1h", 500, date="2026-11-05"))
        self.assertEqual(out["candles"][-1][0], vn_ms("2026-11-06 23:00"))
        self.assertFalse(out["freshness"]["stale"], "ngay da qua khong bao tre")

    def test_bars_capped_at_500(self):
        self.klines.upsert(self.symbol, "1h", flat_rows(H1, 600))
        out = run(server.get_chart_data(self.symbol, "1h", 9999))
        self.assertEqual(len(out["candles"]), 500)

    def test_forming_built_from_m5_and_never_flagged(self):
        now = int(time.time() * 1000)
        start = now // H1 * H1
        self.klines.upsert(self.symbol, "1h", flat_rows(H1, 30))
        m5 = [[start - M5, 1, 1, 1, 1, 999, start - 1]]
        m5 += [[t, 100, 110, 90, 105, 5000, t + M5 - 1] for t in range(start, now - M5, M5)]
        self.klines.upsert(self.symbol, "5m", m5)
        off = run(server.get_chart_data(self.symbol, "1h", 10))
        self.assertTrue(all(c[6] for c in off["candles"]))
        on = run(server.get_chart_data(self.symbol, "1h", 10, include_forming=True))
        if len(m5) > 1:
            last = on["candles"][-1]
            self.assertFalse(last[6])
            self.assertEqual(last[0], start)
            self.assertEqual(last[1], 100)
            self.assertNotIn(start, on["vol_spike"], "nen chua dong khong bao gio la vol_spike")
            self.assertEqual(len(on["vol_avg"]), len(on["candles"]))

    def test_markers_skip_repeat_touches(self):
        codes, _ = self.plan_0711()
        setup = server.SETUPS.get(codes[0])
        at = vn_ms("2026-11-07 09:05")
        server.SETUPS.apply(setup, "IN_ZONE", {}, [
            {"event": "IN_ZONE", "to_status": "IN_ZONE", "price": 75500, "candle_open_time": at, "details": {"repeat": False}},
            {"event": "IN_ZONE", "to_status": "IN_ZONE", "price": 75500, "candle_open_time": at + M5, "details": {"repeat": True}},
            {"event": "CONFIRMED", "to_status": "CONFIRMED", "price": 75500, "candle_open_time": at + M5, "details": {}}])
        out = run(server.get_chart_data(self.symbol, "1h", 48, date="2026-11-07"))
        # TM - #WYCKOFF-TR: marker Wyckoff cua TR nam chung mang, tach bang kind
        self.assertEqual([m["event"] for m in out["markers"] if m["kind"] == "setup"], ["IN_ZONE"])

    def test_declared_tr_used_when_no_saved_tr(self):
        self.klines.upsert(self.symbol, "1h", real_h1_rows())
        self.save_setups(SETUP_A)
        out = run(server.get_chart_data(self.symbol, date="2026-11-07"))
        self.assertEqual(out["tr"]["tr_high"], 76976.7)
        self.assertEqual(out["tr"]["source"], "setup")

    def test_cache_until_new_candle_or_status_change(self):
        codes, _ = self.plan_0711()
        last = real_h1_rows()[-1]
        with sqlite_db.connect(self.klines_path) as conn:
            conn.execute("DELETE FROM klines WHERE tf = '1h' AND open_time = ?", (last[0],))
        args = (self.symbol, "1h", 48, "2026-11-07", False, None)
        first = server._chart_core(*args)
        self.assertIs(server._chart_core(*args), first)
        server.SETUPS.apply(server.SETUPS.get(codes[0]), "IN_ZONE", {}, [
            {"event": "IN_ZONE", "to_status": "IN_ZONE", "price": 75500, "candle_open_time": None, "details": {}}])
        changed = server._chart_core(*args)
        self.assertIsNot(changed, first, "setup doi trang thai thi bo cache")
        self.assertEqual(changed["setups"][0]["status"], "IN_ZONE")
        self.klines.upsert(self.symbol, "1h", [last])
        renewed = server._chart_core(*args)
        self.assertIsNot(renewed, changed, "co nen moi thi bo cache")
        self.assertEqual(renewed["candles"][-1][0], last[0])

    def test_500_m15_under_300ms_from_sqlite(self):
        self.plan_0711()
        self.klines.upsert(self.symbol, "15m", flat_rows(900_000, 3000))
        self.klines.upsert(self.symbol, "5m", flat_rows(M5, 500))
        started = time.perf_counter()
        out = run(server._chart_data(self.symbol, "15m", 500, "", True, None, positions=False))
        elapsed = time.perf_counter() - started
        self.assertEqual(len([c for c in out["candles"] if c[6]]), 500)
        self.assertLess(elapsed, 0.3)


def event(name, **kw):
    return {"event": name, "at": 0, "to_status": name if name in entry_logic.TERMINAL else None,
            "candle_open_time": None, "details": {}, **kw}


class AutoOutcomeTest(unittest.TestCase):
    setup_row = {"status": "EXPIRED", "direction": "long", "stop": 95.0, "target": 110.0}

    def bars(self, *rows):
        return [{"open_time": t, "close_time": t + M5 - 1, "high": h, "low": low} for t, h, low in rows]

    def outcome(self, bars, status="EXPIRED", events=None, **kw):
        history = events or [event("TRIGGERED", candle_open_time=0), event("CONFIRMED"),
                             event(status, at=10 ** 9)]
        return logic.auto_outcome({**self.setup_row, "status": status, **kw}, history, bars)

    def test_tp_first(self):
        self.assertEqual(self.outcome(self.bars((M5, 111, 96), (2 * M5, 100, 90))), "tp_hit")

    def test_sl_first(self):
        self.assertEqual(self.outcome(self.bars((M5, 105, 94), (2 * M5, 120, 100))), "sl_hit")

    def test_both_in_one_candle_is_sl(self):
        self.assertEqual(self.outcome(self.bars((M5, 111, 94))), "sl_hit")

    def test_short_side(self):
        rows = self.bars((M5, 100, 89), (2 * M5, 120, 100))
        self.assertEqual(self.outcome(rows, direction="short", stop=110.0, target=90.0), "tp_hit")

    def test_trigger_candle_itself_not_counted(self):
        self.assertIsNone(self.outcome(self.bars((0, 200, 1))))

    def test_filled_waits_for_tp_or_sl(self):
        self.assertIsNone(self.outcome(self.bars((M5, 100, 96)), status="FILLED"))
        self.assertEqual(self.outcome(self.bars((M5, 100, 96), (2 * M5, 111, 96)), status="FILLED"), "tp_hit")

    def test_stops_at_terminal_event(self):
        history = [event("TRIGGERED", candle_open_time=0), event("INVALIDATED", at=2 * M5)]
        rows = self.bars((M5, 100, 96), (2 * M5, 111, 96))
        self.assertEqual(self.outcome(rows, status="INVALIDATED", events=history), "invalidated")

    def test_never_triggered(self):
        self.assertEqual(self.outcome([], status="INVALIDATED", events=[event("INVALIDATED", at=5)]), "invalidated")
        self.assertEqual(self.outcome([], status="EXPIRED", events=[event("EXPIRED", at=5)]), "not_triggered")

    def test_failed_trigger_is_ignored(self):
        history = [event("TRIGGERED", candle_open_time=0), event("TRIGGER_FAILED"),
                   event("TRIGGERED", candle_open_time=3 * M5), event("CONFIRMED"), event("EXPIRED", at=10 ** 9)]
        rows = self.bars((M5, 200, 96), (4 * M5, 100, 90))
        self.assertEqual(self.outcome(rows, events=history), "sl_hit", "chi tinh tu trigger da qua confirm")

    def test_missed_without_hit(self):
        history = [event("TRIGGERED", candle_open_time=0), event("MISSED"), event("EXPIRED", at=10 ** 9)]
        self.assertEqual(self.outcome(self.bars((M5, 100, 96)), events=history), "missed")

    def test_open_filled_without_trigger_counts_from_fill(self):
        history = [event("FILLED", at=2 * M5)]
        rows = self.bars((M5, 200, 1), (3 * M5, 111, 96))
        self.assertEqual(self.outcome(rows, status="FILLED", events=history), "tp_hit")


class ReviewTest(Base):
    def triggered_setup(self, status="FILLED"):
        codes, _ = self.plan_0711()
        setup = server.SETUPS.get(codes[0])
        now = int(time.time() * 1000)
        start = now // M5 * M5 - 6 * M5
        rows = [[start + i * M5, 75500, 75600, 75400, 75500, 10, start + (i + 1) * M5 - 1] for i in range(5)]
        rows[3][2] = 76400  # TP 76330 cham o nen thu 4
        self.klines.upsert(self.symbol, "5m", rows)
        server.SETUPS.apply(setup, status, {}, [
            {"event": "TRIGGERED", "to_status": "TRIGGERED", "price": 75500, "candle_open_time": start, "details": {}},
            {"event": "CONFIRMED", "to_status": "CONFIRMED", "price": 75500, "candle_open_time": start, "details": {}},
            {"event": status, "from_status": "CONFIRMED", "to_status": status, "price": 75500, "details": {}}])
        return server.SETUPS.get(codes[0]), codes

    def test_override_keeps_auto_outcome(self):
        setup, codes = self.triggered_setup()
        first = run(server.save_review(setup["code"], note="chua gan"))
        self.assertEqual(first["review"]["auto_outcome"], "tp_hit")
        self.assertEqual(first["review"]["outcome"], "tp_hit")
        out = run(server.save_review(setup["code"], outcome="manual_exit", r_multiple=1.2))
        self.assertEqual(out["review"]["outcome"], "manual_exit")
        self.assertEqual(out["review"]["auto_outcome"], "tp_hit")
        self.assertEqual(out["review"]["r_multiple"], 1.2)
        self.assertEqual(out["review"]["note"], "chua gan", "truong khong truyen giu nguyen")
        self.assertEqual(out["review"]["reviewed_by"], "claude")

    def test_validation(self):
        setup, _ = self.triggered_setup()
        with self.assertRaisesRegex(ValueError, "outcome phai"):
            run(server.save_review(setup["code"], outcome="win"))
        with self.assertRaisesRegex(ValueError, "r_multiple"):
            run(server.save_review(setup["code"], r_multiple="x"))
        with self.assertRaisesRegex(ValueError, "can it nhat"):
            run(server.save_review(setup["code"]))
        with self.assertRaisesRegex(ValueError, "khong tim thay"):
            run(server.save_review("BTC-20000101-Z", outcome="be"))

    def test_review_updates_linked_scenario_status(self):
        setup, _ = self.triggered_setup()
        run(server.save_review(setup["code"], outcome="sl_hit"))
        rows = {s["kind"]: s for s in server.PLANS.scenarios(self.symbol, "2026-11-07")}
        self.assertEqual(rows["main"]["status"], "failed")
        self.assertEqual(rows["alt"]["status"], "active", "kich ban gan setup khac khong doi")

    def test_auto_not_saved_when_candles_missing(self):
        setup, _ = self.triggered_setup()
        with sqlite_db.connect(self.klines_path) as conn:
            conn.execute("DELETE FROM klines WHERE tf = '5m'")
        self.assertEqual(server._review_sync([setup]), {}, "khong co nen thi khong ket luan")

    def test_stats(self):
        setup, codes = self.triggered_setup()
        run(server.save_review(setup["code"], outcome="tp_hit", r_multiple=2.0))
        other = server.SETUPS.get(codes[1])
        server.SETUPS.apply(other, "CANCELLED", {}, [
            {"event": "CANCELLED", "to_status": "CANCELLED", "details": {"reason": "x"}}])
        out = server.get_review_stats(self.symbol, "2026-11-01", "2026-11-30")
        self.assertEqual(out["total"]["setups"], 2)
        self.assertEqual(out["total"]["triggered"], 1)
        self.assertEqual(out["total"]["trigger_rate"], 50.0)
        self.assertEqual(out["total"]["win_rate"], 100.0)
        self.assertEqual(out["total"]["avg_r"], 2.0)
        self.assertEqual(out["total"]["outcomes"], {"tp_hit": 1, "not_triggered": 1})
        self.assertEqual([g["key"] for g in out["groups"]], ["rejection"])
        by_direction = server.get_review_stats(self.symbol, "2026-11-01", "2026-11-30", "direction")
        self.assertEqual([g["key"] for g in by_direction["groups"]], ["long", "short"])
        self.assertEqual(server.get_review_stats("", "2026-11-01", "2026-11-30", "symbol")["groups"][0]["key"], self.symbol)
        self.assertEqual(server.get_review_stats(self.symbol, "2026-01-01", "2026-01-31")["total"]["setups"], 0)
        with self.assertRaisesRegex(ValueError, "group_by"):
            server.get_review_stats(self.symbol, "2026-11-01", "2026-11-30", "note")
        with self.assertRaisesRegex(ValueError, "phai truoc"):
            server.get_review_stats(self.symbol, "2026-12-01", "2026-11-01")


class LinkPositionTest(unittest.TestCase):
    groups = [{"position_id": "P1", "side": "short", "opened_ms": 1000, "order_ids": ["a"]},
              {"position_id": "P2", "side": "short", "opened_ms": 5_000_000, "order_ids": ["b"]},
              {"position_id": "P3", "side": "long", "opened_ms": 9_000_000, "order_ids": ["c"]}]

    def test_unlinked_stays_empty(self):
        self.assertEqual(logic.link_positions(self.groups, [], {}, {}), {})

    def test_filled_by_watcher_matches_open_time(self):
        setups = [{"id": "s", "code": "A", "status": "FILLED", "direction": "short"}]
        events = {"s": [{"event": "FILLED", "at": 9, "details": {"position": {"opened_ms": 5_000_100}}}]}
        self.assertEqual(logic.link_positions(self.groups, setups, events, {}), {"P2": "A"})

    def test_filled_wrong_side_not_linked(self):
        setups = [{"id": "s", "code": "A", "status": "FILLED", "direction": "long"}]
        events = {"s": [{"event": "FILLED", "at": 9, "details": {"position": {"opened_ms": 5_000_100}}}]}
        self.assertEqual(logic.link_positions(self.groups, setups, events, {}), {})

    def test_explicit_review_wins(self):
        self.assertEqual(logic.link_positions(self.groups, [], {}, {"B": ["c"]}), {"P3": "B"})


class SafeNextTest(unittest.TestCase):
    def test_allowed(self):
        for value in (BASE, f"{BASE}?symbol=ETHUSDT&date=2026-11-07&mode=review&tf=15m", f"{BASE}/x"):
            self.assertEqual(logic.safe_next(value, BASE), value)

    def test_rejected(self):
        for value in ("", "https://evil.com", "//evil.com", f"{BASE}evil", "/admin", f"{BASE}/../admin",
                      f"{BASE}/%2e%2e/admin", f"{BASE}//evil.com", f"{BASE}\\evil", f"{BASE}/x\r\nSet-Cookie:a",
                      f"{BASE}/%0d%0aX", f"{BASE}/login", f"{BASE}/logout"):
            self.assertEqual(logic.safe_next(value, BASE), BASE, value)


class HttpBase(Base):
    app = None

    @classmethod
    def setUpClass(cls):
        if HttpBase.app is None:
            HttpBase.app = server.build_app()

    def setUp(self):
        super().setUp()
        self.addCleanup(self.drop_admin)
        server.STORE.set_auth("admin", "admin-password-1")
        patcher = mock.patch.object(server.DASH_AUTH, "hasher",
                                    PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1))
        patcher.start()
        self.addCleanup(patcher.stop)
        server.DASH_AUTH._dummy = None
        self.addCleanup(lambda: setattr(server.DASH_AUTH, "_dummy", None))
        self.owner = server.DASH_AUTH.create_user("tan", OWNER_PASS, "owner", actor="test")
        self.viewer = server.DASH_AUTH.create_user("guest", VIEWER_PASS, "viewer", actor="test")

    @staticmethod
    def drop_admin():
        data = server.STORE.load()
        data.pop("auth", None)
        server.STORE._write(data)

    def session(self, coro_fn, ip="10.0.0.1", base_url="https://bamcp.online"):
        """Chay kich ban voi mot client giu cookie, nhu mot trinh duyet."""
        async def main():
            transport = httpx2.ASGITransport(app=self.app, client=(ip, 1234))
            async with httpx2.AsyncClient(transport=transport, base_url=base_url) as client:
                return await coro_fn(client)
        return run(main())

    async def login(self, client, username="tan", password=OWNER_PASS, **form):
        return await client.post(f"{BASE}/login", data={"username": username, "password": password, **form})

    def csrf(self, token):
        return server.DASH_AUTH.session(token)["csrf_token"]

    # TM - #ADMIN-LOGIN - Form dang nhap admin
    async def admin_login(self, client, username="admin", password="admin-password-1", **form):
        return await client.post(server.ADMIN_LOGIN_PATH, data={"username": username, "password": password, **form})


class LoginTest(HttpBase):
    def test_redirects_and_401(self):
        async def main(c):
            page = await c.get(f"{BASE}?symbol=ETHUSDT&mode=review")
            self.assertEqual(page.status_code, 302)
            self.assertEqual(page.headers["location"], f"{BASE}/login?next=%2Fsetup-dashboard%3Fsymbol%3DETHUSDT%26mode%3Dreview")
            self.assertEqual((await c.get(f"{BASE}/login")).status_code, 200)
            for path in ("symbols", "chart", "setups", "events", "snapshots", "positions", "fills", "review-stats"):
                res = await c.get(f"{BASE}/api/{path}")
                self.assertEqual(res.status_code, 401, path)
                self.assertEqual(res.json(), {"error": "unauthorized"})
            self.assertEqual((await c.post(f"{BASE}/api/reviews", json={})).status_code, 401)
            self.assertEqual((await c.get(f"{BASE}/static/dashboard.js")).status_code, 401)
        self.session(main)

    def test_login_flow_and_cookie_flags(self):
        async def main(c):
            res = await self.login(c, next=f"{BASE}?symbol=ETHUSDT")
            self.assertEqual(res.status_code, 303)
            self.assertEqual(res.headers["location"], f"{BASE}?symbol=ETHUSDT")
            cookie = res.headers["set-cookie"].lower()
            for flag in ("httponly", "secure", "samesite=lax", "path=/setup-dashboard", "max-age=43200"):
                self.assertIn(flag, cookie)
            page = await c.get(BASE)
            self.assertEqual(page.status_code, 200)
            self.assertIn("application/json", page.text)
            self.assertEqual((await c.get(f"{BASE}/login", follow_redirects=False)).status_code, 302)
        self.session(main)

    def test_remember_uses_30_days(self):
        async def main(c):
            res = await self.login(c, remember="1")
            self.assertIn(f"max-age={30 * 86400}", res.headers["set-cookie"].lower())
        self.session(main)

    def test_open_redirect_blocked_on_login(self):
        async def main(c):
            for bad in ("https://evil.com", "//evil.com", f"{BASE}evil", "/admin"):
                self.assertEqual((await self.login(c, next=bad)).headers["location"], BASE, bad)
                c.cookies.clear()
        self.session(main)

    def test_token_stored_hashed(self):
        async def main(c):
            await self.login(c)
            return c.cookies.get("bamcp_dash")
        token = self.session(main)
        with sqlite_db.connect(server.SQLITE_PATH) as conn:
            ids = [r[0] for r in conn.execute("SELECT id FROM dashboard_sessions")]
            hashes = [r[0] for r in conn.execute("SELECT password_hash FROM dashboard_users")]
        self.assertNotIn(token, ids)
        self.assertTrue(all(h.startswith("$argon2") for h in hashes))

    def test_admin_session_cannot_enter_dashboard(self):
        async def main(c):
            res = await c.get(BASE, auth=("admin", "admin-password-1"))
            self.assertEqual(res.status_code, 302)
            self.assertEqual((await c.get(f"{BASE}/api/chart", auth=("admin", "admin-password-1"))).status_code, 401)
            self.assertEqual((await self.login(c, "admin", "admin-password-1")).status_code, 401)
        self.session(main)

    def test_dashboard_session_cannot_enter_admin(self):
        async def main(c):
            await self.login(c)
            token = c.cookies.get("bamcp_dash")
            for path in (ADMIN, server.ADMIN_DASH_PATH):
                res = await c.get(path, headers={"cookie": f"bamcp_dash={token}"})
                self.assertEqual(res.status_code, 302, path)
                self.assertTrue(res.headers["location"].startswith(server.ADMIN_LOGIN_PATH), path)
            self.assertEqual((await c.get(ADMIN, auth=("tan", OWNER_PASS))).status_code, 302)
            self.assertEqual((await self.admin_login(c, "tan", OWNER_PASS)).status_code, 401)
            self.assertEqual((await c.post("/mcp", headers={"cookie": f"bamcp_dash={token}"}, json={})).status_code, 401)
        self.session(main)

    def test_prefix_bypass_is_exact(self):
        async def main(c):
            self.assertEqual((await c.get(f"{BASE}x")).status_code, 401)
            for path in (f"{BASE}/../admin", f"{BASE}/../admin/dashboard-accounts"):
                res = await c.get(path)
                self.assertEqual(res.status_code, 302, path)
                self.assertTrue(res.headers["location"].startswith(server.ADMIN_LOGIN_PATH), path)
        self.session(main)

    def test_lockout_after_5_failures(self):
        async def main(c):
            for attempt in range(4):
                self.assertEqual((await self.login(c, password="sai-mat-khau")).status_code, 401, attempt)
            locked = await self.login(c, password="sai-mat-khau")
            self.assertEqual(locked.status_code, 429)
            self.assertIn("tạm khoá 15 phút", locked.text)
            right = await self.login(c)
            self.assertEqual(right.status_code, 429, "dang khoa thi mat khau dung cung khong vao")
        self.session(main)
        rows = server.DASH_AUTH.login_log()
        self.assertEqual(rows[0]["reason"], "locked")
        self.assertEqual(sum(1 for r in rows if not r["ok"]), 6)

        async def other_ip(c):
            self.assertEqual((await self.login(c)).status_code, 303)
        self.session(other_ip, ip="10.0.0.2")

        future = int(time.time() * 1000) + 15 * 60_000 + 1000
        with mock.patch.object(server.DASH_AUTH, "now_ms", lambda: future):
            async def after(c):
                self.assertEqual((await self.login(c, password="sai-mat-khau")).status_code, 401)
                self.assertEqual((await self.login(c)).status_code, 303)
            self.session(after)
        self.assertIsNone(server.DASH_AUTH.locked_until("10.0.0.1"))

    def test_success_resets_failure_count(self):
        async def main(c):
            for _ in range(4):
                await self.login(c, password="sai-mat-khau")
            self.assertEqual((await self.login(c)).status_code, 303)
            c.cookies.clear()
            for _ in range(4):
                self.assertEqual((await self.login(c, password="sai-mat-khau")).status_code, 401)
        self.session(main)

    def test_login_is_logged(self):
        async def main(c):
            await self.login(c)
            await self.login(c, "khong-co", "sai-mat-khau")
        self.session(main)
        ok, bad = server.DASH_AUTH.login_log()[1], server.DASH_AUTH.login_log()[0]
        self.assertEqual((ok["username"], ok["ok"], ok["ip"]), ("tan", 1, "10.0.0.1"))
        self.assertEqual((bad["username"], bad["ok"], bad["reason"]), ("khong-co", 0, "bad_credentials"))

    def test_disabled_account_cannot_login(self):
        server.DASH_AUTH.set_active(self.viewer["id"], False, actor="test")

        async def main(c):
            self.assertEqual((await self.login(c, "guest", VIEWER_PASS)).status_code, 401)
        self.session(main)

    def test_logout_needs_csrf_and_ends_session(self):
        async def main(c):
            await self.login(c)
            token = c.cookies.get("bamcp_dash")
            self.assertEqual((await c.post(f"{BASE}/logout", data={"csrf": "sai"})).status_code, 403)
            res = await c.post(f"{BASE}/logout", data={"csrf": self.csrf(token)})
            self.assertEqual(res.status_code, 303)
            self.assertEqual(res.headers["location"], f"{BASE}/login")
            self.assertIsNone(server.DASH_AUTH.session(token))
            self.assertEqual((await c.get(f"{BASE}/api/symbols")).status_code, 401)
        self.session(main)

    def test_sliding_expiry(self):
        async def main(c):
            await self.login(c)
            token = c.cookies.get("bamcp_dash")
            before = server.DASH_AUTH.session(token)["expires_at"]
            later = int(time.time() * 1000) + 3_600_000
            with mock.patch.object(server.DASH_AUTH, "now_ms", lambda: later):
                res = await c.get(f"{BASE}/api/symbols")
                self.assertIn("max-age=43200", res.headers["set-cookie"].lower())
                self.assertGreater(server.DASH_AUTH.session(token)["expires_at"], before)
            expired = int(time.time() * 1000) + 40 * 3_600_000
            with mock.patch.object(server.DASH_AUTH, "now_ms", lambda: expired):
                self.assertEqual((await c.get(f"{BASE}/api/symbols")).status_code, 401)
        self.session(main)

    def test_security_headers(self):
        async def main(c):
            for res in (await c.get(f"{BASE}/login"), await c.get(BASE), await c.get(f"{BASE}/api/chart")):
                self.assertIn("max-age", res.headers["strict-transport-security"])
                self.assertEqual(res.headers["x-frame-options"], "DENY")
                self.assertIn("script-src 'self'", res.headers["content-security-policy"])
                self.assertNotIn("unsafe", res.headers["content-security-policy"].split("script-src")[1].split(";")[0])
        self.session(main)

    def test_page_has_no_inline_script(self):
        async def main(c):
            await self.login(c)
            page = (await c.get(BASE)).text
            for piece in page.split("<script")[1:]:
                head = piece.split(">", 1)[0]
                self.assertTrue("src=" in head or 'type="application/json"' in head, head)
            self.assertNotIn("cdn", page)
        self.session(main)

    def test_static_assets(self):
        async def main(c):
            await self.login(c)
            js = await c.get(f"{BASE}/static/lightweight-charts.js", headers={"accept-encoding": "gzip"})
            self.assertEqual(js.status_code, 200)
            self.assertEqual(js.headers["content-encoding"], "gzip")
            self.assertLessEqual(int(js.headers["content-length"]), 200 * 1024)
            self.assertIn("javascript", js.headers["content-type"])
            self.assertEqual((await c.get(f"{BASE}/static/server.py")).status_code, 404)
            self.assertIn((await c.get(f"{BASE}/static/..%2F..%2Fserver.py")).status_code, (401, 404))
        self.session(main)

    def test_disabled_returns_404_and_tools_give_no_link(self):
        server._write_json(server.DASH_FILE, {"enabled": False})
        server._DASH_CFG_CACHE.update(key=None, cfg=None)

        async def main(c):
            for path in (BASE, f"{BASE}/login", f"{BASE}/api/chart", f"{BASE}/static/dashboard.js"):
                self.assertEqual((await c.get(path)).status_code, 404, path)
            self.assertEqual((await self.login(c)).status_code, 404)
        self.session(main)
        out = server.save_bias("long", "x", symbol=self.symbol)
        self.assertNotIn("dashboard_url", out)
        self.assertIsNone(server.WATCHER.link({"symbol": self.symbol, "bias_date": "2026-11-07"}))
        self.klines.upsert(self.symbol, "1h", real_h1_rows())
        self.assertNotIn("dashboard_url", run(server.get_chart_data(self.symbol, date="2026-11-07")))

    def test_no_route_can_trade(self):
        """Duoi dashboard chi co GET, tru dang nhap/dang xuat va luu review."""
        posts = sorted(r.path for r in self.app.routes
                       if getattr(r, "path", "").startswith(BASE) and "POST" in (r.methods or ()))
        self.assertEqual(posts, [f"{BASE}/api/reviews", f"{BASE}/login", f"{BASE}/logout"])
        for route in self.app.routes:
            if getattr(route, "path", "").startswith(BASE):
                self.assertFalse({"PUT", "PATCH", "DELETE"} & set(route.methods or ()), route.path)


class RoleTest(HttpBase):
    def positions_block(self):
        return {"errors": {}, "positions": [
            {"side": "short", "size": 2.5, "entry_price": 79000.0, "mark_price": 76000.0, "unrealized_pnl": 121.5,
             "leverage": 5, "opened_ms": 1, "api_key": "KHONG-DUOC-LO"}]}

    def filled_setup(self):
        codes, _ = self.plan_0711()
        setup = server.SETUPS.get(codes[0])
        at = int(time.time() * 1000)
        server.SETUPS.apply(setup, "FILLED", {}, [
            {"event": "FILLED", "from_status": "CONFIRMED", "to_status": "FILLED", "price": 75512.5, "details": {
                "position": {"side": "long", "size": 3.0, "entry_price": 75512.5, "opened_ms": at}, "source": "okx"}}])
        server.SETUPS.system_event("UNPLANNED_POSITION", {"symbol": self.symbol, "position": {"size": 9}}, price=1.0, at=at)
        server.PLANS.save_review(setup["id"], outcome="tp_hit", r_multiple=1.5, note="x", reviewed_by="tan", fill_ids=["okx-order-1"])
        return codes

    def test_viewer_sees_plan_but_no_money(self):
        codes = self.filled_setup()

        async def main(c):
            await self.login(c, "guest", VIEWER_PASS)
            token = c.cookies.get("bamcp_dash")
            today = {"symbol": self.symbol, "date": "2026-11-07"}
            chart = (await c.get(f"{BASE}/api/chart", params={**today, "tf": "1h", "bars": 48})).json()
            self.assertEqual(len(chart["setups"]), 2)
            self.assertNotIn("positions", chart)
            self.assertEqual(len(chart["scenarios"]), 2)
            self.assertIsNone(chart["setups"][0]["last_event"]["price"], "gia khop lay tu san")
            for path in ("positions", "fills"):
                self.assertEqual((await c.get(f"{BASE}/api/{path}", params=today)).status_code, 403, path)
            res = await c.post(f"{BASE}/api/reviews", json={"setup_code": codes[0], "outcome": "be"},
                               headers={"x-csrf-token": self.csrf(token)})
            self.assertEqual(res.status_code, 403)
            stats = (await c.get(f"{BASE}/api/review-stats", params={"from": "2026-11-01", "to": "2026-11-30"})).json()
            self.assertEqual(stats["total"]["setups"], 2)
            self.assertNotIn("positions", stats)
            setups = (await c.get(f"{BASE}/api/setups", params=today)).json()["setups"]
            self.assertNotIn("exchange_fill_ids", setups[0]["review"])
            self.assertIsNone(setups[0]["last_event"]["price"])
            self.assertEqual((await c.get(f"{BASE}/api/snapshots", params=today)).status_code, 200)
            self.assertEqual((await c.get(BASE)).status_code, 200)
            events = (await c.get(f"{BASE}/api/events", params={"symbol": self.symbol})).json()["events"]
            names = [e["event"] for e in events]
            self.assertIn("FILLED", names)
            self.assertNotIn("UNPLANNED_POSITION", names)
            self.assertIsNone(next(e for e in events if e["event"] == "FILLED")["price"])
            text = json.dumps([chart, setups, events])
            for leak in ("75512.5", "position_key", "entry_price", "unrealized_pnl", "okx-order-1"):
                self.assertNotIn(leak, text, leak)
        self.session(main)

    def test_viewer_never_gets_notes(self):
        """Ca ke hoach dang chay lan snapshot: note la chu tu do, thuc te co nhac vi the that."""
        self.klines.upsert(self.symbol, "1h", real_h1_rows())
        code = self.save_setups({**SETUP_A, "note": "NOTE-SETUP nguoc 2 short dang mo"})["codes"][0]
        server.save_scenarios(self.symbol, [scenario(setup_codes=[code], points=[
            {"t": vn_ms("2026-11-07 16:00"), "price": 75500, "note": "NOTE-DIEM"},
            {"t": vn_ms("2026-11-07 20:00"), "price": 76330}])], bias_date="2026-11-07")
        server.PLANS.save_review(server.SETUPS.get(code)["id"], outcome="be", r_multiple=None,
                                 note="NOTE-REVIEW", reviewed_by="tan")
        day = {"symbol": self.symbol, "date": "2026-11-07"}

        async def responses(c):
            charts = [await c.get(f"{BASE}/api/chart", params={**day, "revision": r}) for r in ("", "1", "2", "origin")]
            return charts, await c.get(f"{BASE}/api/setups", params=day)

        async def as_viewer(c):
            await self.login(c, "guest", VIEWER_PASS)
            charts, setups = await responses(c)
            for res in (*charts, setups):
                self.assertEqual(res.status_code, 200)
                self.assertNotIn("NOTE-", res.text, res.url)
            self.assertNotIn("note", setups.json()["setups"][0])
            self.assertEqual(setups.json()["setups"][0]["review"]["outcome"], "be", "ket qua review van xem duoc")
            self.assertEqual(len(charts[0].json()["scenarios"][0]["points"]), 2, "van ve duoc kich ban")

        async def as_owner(c):
            await self.login(c)
            charts, setups = await responses(c)
            self.assertIn("NOTE-SETUP", charts[0].text)
            self.assertIn("NOTE-DIEM", charts[0].text)
            self.assertIn("NOTE-SETUP", charts[3].text, "ke hoach goc cua owner van co note")
            self.assertIn("NOTE-REVIEW", setups.text)
        self.session(as_viewer)
        self.session(as_owner)

    def test_bias_notes_not_sent_to_browser(self):
        """notes cua save_bias thuong ghi vi the that: chi con trong snapshot, khong ra API."""
        self.klines.upsert(self.symbol, "1h", real_h1_rows())
        server.save_bias("neutral", "x", symbol=self.symbol, notes="2 short OKX 2.5 hd @79000", date="2026-11-07")

        async def main(c):
            await self.login(c)
            day = {"symbol": self.symbol, "date": "2026-11-07"}
            for revision in ("", "1", "origin"):
                res = await c.get(f"{BASE}/api/chart", params={**day, "revision": revision})
                self.assertEqual(res.status_code, 200, revision)
                self.assertNotIn("79000", res.text, revision)
        self.session(main)
        self.assertIn("79000", server.PLANS.snapshot(self.symbol, "2026-11-07", 1)["payload"]["bias"]["notes"])

    def test_owner_sees_positions_from_exchange(self):
        self.filled_setup()

        async def main(c):
            await self.login(c)
            with (mock.patch.object(server, "_account_enabled", lambda: True),
                  mock.patch.object(server, "_open_positions", mock.AsyncMock(return_value=self.positions_block()))):
                chart = (await c.get(f"{BASE}/api/chart", params={"symbol": self.symbol, "date": "2026-11-07"})).json()
                pos = (await c.get(f"{BASE}/api/positions", params={"symbol": self.symbol})).json()
            self.assertEqual(chart["positions"]["unrealized_pnl"], 121.5)
            self.assertEqual(pos["positions"][0]["entry_price"], 79000.0)
            self.assertNotIn("KHONG-DUOC-LO", json.dumps(pos), "chi tra cac truong cho phep")
        self.session(main)

    def test_exchange_called_once_per_30s(self):
        loader = mock.AsyncMock(return_value=self.positions_block())

        async def main(c):
            await self.login(c)
            with (mock.patch.object(server, "_account_enabled", lambda: True),
                  mock.patch.object(server, "_open_positions", loader)):
                for _ in range(3):
                    await c.get(f"{BASE}/api/positions", params={"symbol": self.symbol})
        self.session(main)
        self.assertEqual(loader.await_count, 1)

    def test_hide_pnl(self):
        server._write_json(server.DASH_FILE, {"hide_pnl": True})
        server._DASH_CFG_CACHE.update(key=None, cfg=None)

        async def main(c):
            await self.login(c)
            with (mock.patch.object(server, "_account_enabled", lambda: True),
                  mock.patch.object(server, "_open_positions", mock.AsyncMock(return_value=self.positions_block()))):
                pos = (await c.get(f"{BASE}/api/positions", params={"symbol": self.symbol})).json()
            self.assertIsNone(pos["unrealized_pnl"])
            self.assertTrue(pos["pnl_hidden"])
            self.assertNotIn("121.5", json.dumps(pos))
        self.session(main)

    def test_exchange_error_text_not_returned(self):
        boom = mock.AsyncMock(side_effect=RuntimeError("https://x/?signature=SECRETSIGN"))

        async def main(c):
            await self.login(c)
            with (mock.patch.object(server, "_account_enabled", lambda: True),
                  mock.patch.object(server, "_open_positions", boom)):
                return (await c.get(f"{BASE}/api/positions", params={"symbol": self.symbol})).text
        self.assertNotIn("SECRETSIGN", self.session(main))

    def test_fills_mark_unlinked(self):
        codes = self.filled_setup()
        at = server.SETUPS.events_of([server.SETUPS.get(codes[0])["id"]])[server.SETUPS.get(codes[0])["id"]][-1]
        opened = at["details"]["position"]["opened_ms"]
        day = {"errors": {}, "positions": [
            {"position_id": "BTCUSDT-P1", "side": "long", "closed": True, "carried_in": False, "fill_count": 1,
             "order_ids": ["o1"], "opened_at": "2026-11-07 09:00:00", "closed_at": "", "realized_pnl": 5.0, "fee": 0.1,
             "fills": [{"ts": opened, "price": 75512.5, "qty": 3.0, "realized_pnl": 0}]},
            {"position_id": "BTCUSDT-P2", "side": "short", "closed": False, "carried_in": False, "fill_count": 1,
             "order_ids": ["o2"], "opened_at": "2026-11-07 09:30:00", "closed_at": "", "realized_pnl": 0.0, "fee": 0.1,
             "fills": [{"ts": opened + 1, "price": 79000.0, "qty": 2.5, "realized_pnl": 0}]}]}

        async def main(c):
            await self.login(c)
            token = c.cookies.get("bamcp_dash")
            with (mock.patch.object(server, "_account_enabled", lambda: True),
                  mock.patch.object(server, "_account_day", mock.AsyncMock(return_value=day))):
                out = (await c.get(f"{BASE}/api/fills", params={"symbol": self.symbol, "date": "2026-11-07"})).json()
                res = await c.post(f"{BASE}/api/reviews", json={"setup_code": codes[0], "outcome": "tp_hit", "r_multiple": 2,
                                                                "note": "ok"}, headers={"x-csrf-token": self.csrf(token)})
            self.assertEqual([p["setup_code"] for p in out["positions"]], [codes[0], None])
            self.assertEqual(out["unlinked"], 1)
            self.assertEqual(out["positions"][1]["entry_price"], 79000.0)
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json()["review"]["exchange_fill_ids"], ["o1"])
            self.assertEqual(res.json()["review"]["reviewed_by"], "tan")
        self.session(main)

    def test_owner_review_requires_csrf(self):
        codes, _ = self.plan_0711()

        async def main(c):
            await self.login(c)
            token = c.cookies.get("bamcp_dash")
            body = {"setup_code": codes[0], "note": "x"}
            self.assertEqual((await c.post(f"{BASE}/api/reviews", json=body)).status_code, 403)
            res = await c.post(f"{BASE}/api/reviews", json=body, headers={"x-csrf-token": "sai"})
            self.assertEqual(res.status_code, 403)
            res = await c.post(f"{BASE}/api/reviews", json=body, headers={"x-csrf-token": self.csrf(token)})
            self.assertEqual(res.status_code, 200)
            bad = await c.post(f"{BASE}/api/reviews", json={"setup_code": codes[0], "outcome": "win"},
                               headers={"x-csrf-token": self.csrf(token)})
            self.assertEqual(bad.status_code, 400)
        self.session(main)

    def test_no_secret_in_any_response(self):
        codes, _ = self.plan_0711()
        setup = server.SETUPS.get(codes[0])
        ids = server.SETUPS.apply(setup, "IN_ZONE", {}, [
            {"event": "IN_ZONE", "to_status": "IN_ZONE", "price": 75500, "candle_open_time": None, "details": {}}])
        server.SETUPS.mark_notified(ids[-1], f"loi gui {SECRETS[0]} {SECRETS[1]}")

        async def main(c):
            await self.login(c)
            day = {"symbol": self.symbol, "date": "2026-11-07"}
            pages = [await c.get(BASE)] + [await c.get(f"{BASE}/api/{p}", params=day)
                                           for p in ("symbols", "chart", "setups", "events", "snapshots", "positions", "fills", "review-stats")]
            events = pages[4].json()["events"]
            self.assertEqual(next(e for e in events if e["event"] == "IN_ZONE")["notify"], "error")
            for page in pages:
                for secret in (*SECRETS, "password_hash", "$argon2"):
                    self.assertNotIn(secret, page.text, page.url)
        self.session(main)


class AdminAccountsTest(HttpBase):
    def session(self, coro_fn, ip="10.0.0.1", **kwargs):
        """Moi kich ban o day deu chay sau khi da dang nhap trang admin."""
        async def logged_in(c):
            self.assertEqual((await self.admin_login(c)).status_code, 303)
            return await coro_fn(c)
        return super().session(logged_in, ip, **kwargs)

    async def action(self, client, **payload):
        return await client.post(server.ADMIN_DASH_ACTION_PATH, json=payload)

    def test_page_needs_admin_and_lists_users(self):
        async def anonymous(c):
            self.assertEqual((await c.get(server.ADMIN_DASH_PATH)).status_code, 302)
        HttpBase.session(self, anonymous)

        async def main(c):
            page = await c.get(server.ADMIN_DASH_PATH)
            self.assertEqual(page.status_code, 200)
            for text in ("Dashboard accounts", "guest", "tan", "Audit log", "Lịch sử đăng nhập"):
                self.assertIn(text, page.text)
            self.assertNotIn("$argon2", page.text)
            self.assertIn("Dashboard accounts", (await c.get(ADMIN)).text, "co muc tren menu")
        self.session(main)

    def test_created_viewer_logs_in_immediately(self):
        async def main(c):
            res = await self.action(c, action="create", username="New.User-1", password="new-password-1")
            self.assertEqual(res.status_code, 400, "username phai viet thuong")
            res = await self.action(c, action="create", username="new.user-1", password="new-password-1")
            self.assertEqual(res.status_code, 200)
            self.assertIsNone(res.json()["generated_password"])
            self.assertEqual(server.DASH_AUTH.user("new.user-1")["role"], "viewer", "mac dinh viewer")
            self.assertEqual((await self.login(c, "new.user-1", "new-password-1")).status_code, 303)
            self.assertEqual((await c.get(f"{BASE}/api/positions")).status_code, 403)
        self.session(main)

    def test_create_validation(self):
        async def main(c):
            for payload in ({"username": "ab", "password": "new-password-1"},
                            {"username": "x" * 33, "password": "new-password-1"},
                            {"username": "bad name", "password": "new-password-1"},
                            {"username": "okname", "password": "short"},
                            {"username": "tan", "password": "new-password-1"},
                            {"username": "okname", "password": "new-password-1", "role": "root"}):
                res = await self.action(c, action="create", **payload)
                self.assertEqual(res.status_code, 400, payload)
        self.session(main)

    def test_generated_password_shown_once_and_works(self):
        async def main(c):
            res = await self.action(c, action="create", username="randomuser", role="viewer", generate=True)
            password = res.json()["generated_password"]
            self.assertGreaterEqual(len(password), 10)
            self.assertEqual((await self.login(c, "randomuser", password)).status_code, 303)
            page = await c.get(server.ADMIN_DASH_PATH)
            self.assertNotIn(password, page.text)
            self.assertNotIn(password, json.dumps(server.DASH_AUTH.audit_log()))
        self.session(main)

    def changing_kills_session(self, change):
        async def main(c):
            await self.login(c, "guest", VIEWER_PASS)
            self.assertEqual((await c.get(f"{BASE}/api/symbols")).status_code, 200)
            res = await self.action(c, **change)
            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual((await c.get(f"{BASE}/api/symbols")).status_code, 401)
            self.assertEqual((await c.get(BASE)).status_code, 302)
        self.session(main)

    def test_disable_logs_out(self):
        self.changing_kills_session({"action": "set_active", "id": self.viewer["id"], "active": False})

    def test_password_reset_logs_out(self):
        self.changing_kills_session({"action": "reset_password", "id": self.viewer["id"], "password": "another-password"})

    def test_role_change_logs_out(self):
        self.changing_kills_session({"action": "set_role", "id": self.viewer["id"], "role": "owner"})

    def test_delete_logs_out(self):
        self.changing_kills_session({"action": "delete", "id": self.viewer["id"]})

    def test_last_owner_protected(self):
        async def main(c):
            oid = self.owner["id"]
            for change in ({"action": "delete", "id": oid}, {"action": "set_role", "id": oid, "role": "viewer"},
                           {"action": "set_active", "id": oid, "active": False}):
                res = await self.action(c, **change)
                self.assertEqual(res.status_code, 400, change)
                self.assertIn("owner cuoi cung", res.json()["error"])
            await self.action(c, action="set_role", id=self.viewer["id"], role="owner")
            self.assertEqual((await self.action(c, action="set_role", id=oid, role="viewer")).status_code, 200)
            res = await self.action(c, action="delete", id=self.viewer["id"])
            self.assertEqual(res.status_code, 400, "guest gio la owner cuoi cung")
        self.session(main)

    def test_audit_logs_every_change_without_password(self):
        async def main(c):
            await self.action(c, action="create", username="audited", password="very-secret-pass")
            uid = server.DASH_AUTH.user("audited")["id"]
            await self.action(c, action="set_role", id=uid, role="owner")
            await self.action(c, action="reset_password", id=uid, password="another-secret-pass")
            await self.action(c, action="set_active", id=uid, active=False)
            await self.action(c, action="set_active", id=uid, active=True)
            await self.action(c, action="delete", id=uid)
            page = await c.get(server.ADMIN_DASH_PATH)
            self.assertIn("reset_password", page.text)
        self.session(main)
        rows = [r for r in server.DASH_AUTH.audit_log() if r["target"] == "audited"]
        self.assertEqual([r["action"] for r in rows[::-1]],
                         ["create", "set_role", "reset_password", "disable", "enable", "delete"])
        self.assertEqual(rows[0]["actor"], "admin")
        self.assertEqual(rows[0]["ip"], "10.0.0.1")
        text = json.dumps(rows)
        self.assertNotIn("very-secret-pass", text)
        self.assertNotIn("another-secret-pass", text)

    def test_config_save(self):
        async def main(c):
            res = await self.action(c, action="config_save", config={"poll_seconds": 1})
            self.assertEqual(res.status_code, 400)
            res = await self.action(c, action="config_save", config={"default_tf": "2h"})
            self.assertEqual(res.status_code, 400)
            res = await self.action(c, action="config_save", config={"poll_seconds": "30", "hide_pnl": True})
            self.assertEqual(res.status_code, 200)
            self.assertEqual(server._dash_cfg()["poll_seconds"], 30)
            self.assertTrue(server._dash_cfg()["hide_pnl"])
        self.session(main)
        self.assertEqual(server.DASH_AUTH.audit_log()[0]["action"], "config_save")

    def test_action_requires_json_content_type(self):
        async def main(c):
            body = json.dumps({"action": "create", "username": "evil", "password": "new-password-1", "role": "owner"})
            for content_type in ("text/plain", "text/plain; x=application/json",
                                 "application/x-www-form-urlencoded"):
                res = await c.post(server.ADMIN_DASH_ACTION_PATH, content=body,
                                   headers={"content-type": content_type})
                self.assertEqual(res.status_code, 415, content_type)
        self.session(main)
        with self.assertRaises(ValueError):
            server.DASH_AUTH.user("evil")

    def test_setup_token_required_without_admin_password(self):
        self.drop_admin()

        async def main(c):
            res = await c.post(server.ADMIN_DASH_ACTION_PATH, json={"action": "create", "username": "xyz", "password": "new-password-1"})
            self.assertEqual(res.status_code, 403)
        HttpBase.session(self, main)


# TM - #ADMIN-LOGIN - Form dang nhap admin
class AdminLoginTest(HttpBase):
    AUTH = ("admin", "admin-password-1")

    def test_page_redirects_to_form_without_popup(self):
        async def main(c):
            res = await c.get(ADMIN)
            self.assertEqual(res.status_code, 302)
            self.assertEqual(res.headers["location"], f"{server.ADMIN_LOGIN_PATH}?next=%2Fadmin")
            self.assertNotIn("www-authenticate", res.headers)
            res = await c.get(f"{server.ADMIN_SCHEDULES_PATH}?tab=x")
            self.assertEqual(res.headers["location"], f"{server.ADMIN_LOGIN_PATH}?next=%2Fadmin%2Fschedules%3Ftab%3Dx")
            form = await c.get(res.headers["location"])
            self.assertEqual(form.status_code, 200)
            self.assertNotIn("www-authenticate", form.headers)
            self.assertEqual(form.headers["cache-control"], "no-store")
            self.assertEqual(form.headers["x-frame-options"], "DENY")
            self.assertIn("form-action 'self'", form.headers["content-security-policy"])
            for text in (f'action="{server.ADMIN_LOGIN_PATH}"', 'type="password"', 'autocomplete="current-password"',
                         'name="next" value="/admin/schedules?tab=x"'):
                self.assertIn(text, form.text)
            self.assertNotIn("<script", form.text)
        self.session(main)

    def test_action_without_session_gets_json_401_without_popup(self):
        async def main(c):
            res = await c.post(server.ADMIN_SAVE_PATH, json={})
            self.assertEqual(res.status_code, 401)
            self.assertNotIn("www-authenticate", res.headers)
            self.assertIn("đăng nhập", res.json()["error"])
        self.session(main)

    def test_login_flow_and_cookie_flags(self):
        async def main(c):
            res = await self.admin_login(c, next=f"{server.ADMIN_SCHEDULES_PATH}?tab=x")
            self.assertEqual(res.status_code, 303)
            self.assertEqual(res.headers["location"], f"{server.ADMIN_SCHEDULES_PATH}?tab=x")
            cookie = res.headers["set-cookie"].lower()
            for flag in ("bamcp_admin=", "httponly", "secure", "samesite=lax", "path=/admin", "max-age=43200"):
                self.assertIn(flag, cookie)
            page = await c.get(ADMIN)
            self.assertEqual(page.status_code, 200)
            self.assertIn(f'action="{server.ADMIN_LOGOUT_PATH}"', page.text)
            again = await c.get(f"{server.ADMIN_LOGIN_PATH}?next={server.ADMIN_ORB_PATH}")
            self.assertEqual(again.status_code, 302, "da dang nhap thi khong hien form nua")
            self.assertEqual(again.headers["location"], server.ADMIN_ORB_PATH)
        self.session(main)

    def test_remember_uses_30_days(self):
        async def main(c):
            res = await self.admin_login(c, remember="1")
            self.assertIn(f"max-age={30 * 86400}", res.headers["set-cookie"].lower())
        self.session(main)

    def test_plain_http_cookie_is_not_secure_and_still_works(self):
        async def main(c):
            res = await self.admin_login(c)
            self.assertEqual(res.status_code, 303)
            self.assertNotIn("secure", res.headers["set-cookie"].lower())
            self.assertEqual((await c.get(ADMIN)).status_code, 200)
        self.session(main, base_url="http://127.0.0.1:8848")

    def test_wrong_credentials_show_form_again(self):
        async def main(c):
            for username, password in (("admin", "sai-mat-khau-1"), ("khong-co", "admin-password-1"), ("", "")):
                res = await self.admin_login(c, username, password)
                self.assertEqual(res.status_code, 401, username)
                self.assertNotIn("set-cookie", res.headers)
                self.assertIn("Sai tên đăng nhập hoặc mật khẩu", res.text)
                self.assertNotIn("sai-mat-khau-1", res.text)
            self.assertIn('value="khong-co"', (await self.admin_login(c, "khong-co", "x")).text)
            self.assertEqual((await c.get(ADMIN)).status_code, 302)
        self.session(main)

    def test_lockout_after_5_failures(self):
        async def main(c):
            for attempt in range(4):
                self.assertEqual((await self.admin_login(c, password="sai")).status_code, 401, attempt)
            self.assertEqual((await self.admin_login(c, password="sai")).status_code, 429)
            res = await self.admin_login(c)
            self.assertEqual(res.status_code, 429, "dang khoa thi mat khau dung cung bi chan")
            self.assertNotIn("set-cookie", res.headers)
        self.session(main)

        async def allowed(c):
            self.assertEqual((await self.admin_login(c)).status_code, 303)
        self.session(allowed, ip="10.0.0.2")

        future = int(time.time() * 1000) + 16 * 60_000
        with mock.patch.object(server.DASH_AUTH, "now_ms", lambda: future):
            self.session(allowed)

    def test_lockout_is_separate_from_dashboard(self):
        async def main(c):
            for _ in range(5):
                await self.admin_login(c, password="sai")
            self.assertEqual((await self.admin_login(c)).status_code, 429)
            self.assertEqual((await self.login(c)).status_code, 303, "sai ben admin khong khoa dashboard")
            self.assertEqual((await self.admin_login(c)).status_code, 429, "dung ben dashboard khong mo khoa admin")
        self.session(main)

        async def dashboard_failures(c):
            for _ in range(5):
                await self.login(c, password="sai")
            self.assertEqual((await self.login(c)).status_code, 429)
            self.assertEqual((await self.admin_login(c)).status_code, 303, "sai ben dashboard khong khoa admin")
        self.session(dashboard_failures, ip="10.0.0.3")

    def test_success_clears_failures(self):
        async def main(c):
            for _ in range(4):
                await self.admin_login(c, password="sai")
            self.assertEqual((await self.admin_login(c)).status_code, 303)
            for attempt in range(4):
                self.assertEqual((await self.admin_login(c, password="sai")).status_code, 401, attempt)
        self.session(main)

    def test_open_redirect_blocked(self):
        async def main(c):
            for bad in ("https://evil.com", "//evil.com", f"{ADMIN}evil", "/mcp", server.ADMIN_LOGIN_PATH,
                        server.ADMIN_LOGOUT_PATH, f"{ADMIN}/../mcp", BASE):
                self.assertEqual((await self.admin_login(c, next=bad)).headers["location"], ADMIN, bad)
                c.cookies.clear()
                form = await c.get(server.ADMIN_LOGIN_PATH, params={"next": bad})
                self.assertIn(f'name="next" value="{ADMIN}"', form.text, bad)
        self.session(main)

    def test_basic_auth_does_not_open_admin(self):
        async def main(c):
            self.assertEqual((await c.get(ADMIN, auth=self.AUTH)).status_code, 302)
            self.assertEqual((await c.post(server.ADMIN_SAVE_PATH, json={}, auth=self.AUTH)).status_code, 401)
        self.session(main)

    def test_admin_session_opens_only_admin(self):
        async def main(c):
            await self.admin_login(c)
            cookie = {"cookie": f"bamcp_admin={c.cookies.get('bamcp_admin')}"}
            res = await c.post("/mcp", headers=cookie, json={})
            self.assertEqual(res.status_code, 401)
            self.assertIn("www-authenticate", res.headers)
            self.assertEqual((await c.get(BASE, headers=cookie)).status_code, 302)
            self.assertEqual((await c.get(f"{BASE}/api/chart", headers=cookie)).status_code, 401)
            self.assertEqual((await c.get(f"{ADMIN}x", headers=cookie)).status_code, 401)
        self.session(main)

    def test_mcp_keeps_basic_auth(self):
        async def stub(scope, receive, send):
            await JSONResponse({"ok": True})(scope, receive, send)
        app = server.BasicAuthMiddleware(stub, store=server.STORE, realm="BAMCP",
                                         session_prefix=ADMIN, session_gate=server._admin_gate)

        async def main():
            transport = httpx2.ASGITransport(app=app)
            async with httpx2.AsyncClient(transport=transport, base_url="https://bamcp.online") as c:
                res = await c.post("/mcp")
                self.assertEqual(res.status_code, 401)
                self.assertIn('Basic realm="BAMCP"', res.headers["www-authenticate"])
                self.assertEqual((await c.post("/mcp", auth=("admin", "sai"))).status_code, 401)
                self.assertEqual((await c.post("/mcp", auth=self.AUTH)).status_code, 200)
        run(main())

    def test_token_stored_hashed(self):
        async def main(c):
            await self.admin_login(c)
            return c.cookies.get("bamcp_admin")
        token = self.session(main)
        with sqlite_db.connect(server.SQLITE_PATH) as conn:
            ids = [r[0] for r in conn.execute("SELECT id FROM admin_sessions")]
        self.assertEqual(len(ids), 1)
        self.assertNotIn(token, ids)

    def test_logout(self):
        async def main(c):
            await self.admin_login(c)
            token = c.cookies.get("bamcp_admin")
            self.assertEqual((await c.get(server.ADMIN_LOGOUT_PATH)).status_code, 405, "chi POST")
            self.assertEqual((await c.get(ADMIN)).status_code, 200)
            res = await c.post(server.ADMIN_LOGOUT_PATH)
            self.assertEqual(res.status_code, 303)
            self.assertEqual(res.headers["location"], server.ADMIN_LOGIN_PATH)
            self.assertIsNone(c.cookies.get("bamcp_admin"))
            self.assertEqual((await c.get(ADMIN)).status_code, 302)
            replay = await c.get(ADMIN, headers={"cookie": f"bamcp_admin={token}"})
            self.assertEqual(replay.status_code, 302, "token cu khong dung lai duoc")
        self.session(main)

    def test_logout_without_cookie_changes_nothing(self):
        """Form cross-site khong gui kem cookie SameSite=Lax, nen khong dang xuat ho duoc."""
        async def main(c):
            res = await c.post(server.ADMIN_LOGOUT_PATH)
            self.assertEqual(res.status_code, 303)
            self.assertNotIn("set-cookie", res.headers)
        self.session(main)

    def test_password_change_ends_sessions(self):
        async def main(c):
            await self.admin_login(c)
            res = await c.post(server.ADMIN_SAVE_PATH, json={"username": "admin", "password": "new-password-1",
                                                             "password2": "new-password-1"})
            self.assertEqual(res.status_code, 200, res.text)
            self.assertTrue(res.json()["reload"])
            self.assertEqual((await c.get(ADMIN)).status_code, 302, "phien cu het hieu luc ngay")
            self.assertEqual((await self.admin_login(c)).status_code, 401, "mat khau cu")
            self.assertEqual((await self.admin_login(c, password="new-password-1")).status_code, 303)
            self.assertEqual((await c.get(ADMIN)).status_code, 200)
        self.session(main)

    def test_session_expires(self):
        async def main(c):
            await self.admin_login(c)
            now = int(time.time() * 1000)
            with mock.patch.object(server.ADMIN_SESSIONS, "now_ms", lambda: now + 11 * 3_600_000):
                self.assertEqual((await c.get(ADMIN)).status_code, 200)
            with mock.patch.object(server.ADMIN_SESSIONS, "now_ms", lambda: now + 13 * 3_600_000):
                self.assertEqual((await c.get(ADMIN)).status_code, 302)
        self.session(main)

    def test_setup_mode_stays_open_until_password_is_set(self):
        self.drop_admin()

        async def main(c):
            page = await c.get(ADMIN)
            self.assertEqual(page.status_code, 200)
            self.assertIn('id="setup_token"', page.text)
            self.assertNotIn(server.ADMIN_LOGOUT_PATH, page.text, "chua co mat khau thi khong co gi de dang xuat")
            res = await c.get(server.ADMIN_LOGIN_PATH)
            self.assertEqual(res.status_code, 302)
            self.assertEqual(res.headers["location"], ADMIN)
            payload = {"username": "admin", "password": "first-password-1", "password2": "first-password-1"}
            self.assertEqual((await c.post(server.ADMIN_SAVE_PATH, json=payload)).status_code, 403)
            payload["setup_token"] = server.STORE.setup_token()
            res = await c.post(server.ADMIN_SAVE_PATH, json=payload)
            self.assertEqual(res.status_code, 200, res.text)
            self.assertTrue(res.json()["reload"])
            res = await c.get(ADMIN)
            self.assertEqual(res.status_code, 302, "vua dat mat khau xong thi phai dang nhap")
            self.assertTrue(res.headers["location"].startswith(server.ADMIN_LOGIN_PATH))
            self.assertEqual((await self.admin_login(c, password="first-password-1")).status_code, 303)
        self.session(main)

    def test_auth_disabled_has_no_login(self):
        async def main(c):
            res = await c.get(server.ADMIN_LOGIN_PATH)
            self.assertEqual(res.status_code, 302)
            self.assertEqual(res.headers["location"], ADMIN)
        with mock.patch.object(server, "AUTH_ENABLED", False):
            self.session(main)
            self.assertNotIn("logout", [key for key, _label, _path in server._admin_nav()])

    def test_logout_is_a_post_button_not_a_link(self):
        nav = [("admin", "Cài đặt", "/admin"), ("logout", "Đăng xuất", "/admin/logout")]
        page = admin_ui.page(title="x", active="admin", nav=nav, body="")
        self.assertIn('<form class="logout" method="post" action="/admin/logout">', page)
        self.assertNotIn('href="/admin/logout"', page)
        self.assertNotIn('class="logout"', admin_ui.page(title="x", active="admin", nav=nav[:1], body=""))


class CliTest(HttpBase):
    def create(self, username, password):
        with mock.patch("sys.stdin", io.StringIO(password + "\n")), mock.patch("sys.stdout", io.StringIO()) as out:
            code = bamcp_cli.main(["dashboard", "create-owner", username, "--password-stdin"])
        return code, out.getvalue()

    def test_creates_owner_that_can_log_in(self):
        code, text = self.create("rescue", "rescue-password-1")
        self.assertEqual(code, 0)
        self.assertIn("rescue", text)
        self.assertNotIn("rescue-password-1", text)
        self.assertEqual(server.DASH_AUTH.user("rescue")["role"], "owner")

        async def main(c):
            self.assertEqual((await self.login(c, "rescue", "rescue-password-1")).status_code, 303)
        self.session(main)
        self.assertEqual(server.DASH_AUTH.audit_log()[0]["actor"], "cli")

    def test_restores_existing_account(self):
        server.DASH_AUTH.set_active(self.viewer["id"], False, actor="test")
        code, text = self.create("guest", "restored-password")
        self.assertEqual(code, 0)
        self.assertIn("khoi phuc", text)
        user = server.DASH_AUTH.user("guest")
        self.assertEqual((user["role"], user["is_active"]), ("owner", 1))
        self.assertIsNotNone(server.DASH_AUTH.authenticate("guest", "restored-password"))

    def test_bad_password_fails_cleanly(self):
        with mock.patch("sys.stderr", io.StringIO()):
            code, _ = self.create("rescue", "short")
        self.assertEqual(code, 1)

    def test_generates_password_without_tty(self):
        with mock.patch("sys.stdin", io.StringIO("")), mock.patch("sys.stdout", io.StringIO()) as out:
            self.assertEqual(bamcp_cli.main(["dashboard", "create-owner", "auto-pass"]), 0)
        password = out.getvalue().split(": ")[-1].strip()
        self.assertIsNotNone(server.DASH_AUTH.authenticate("auto-pass", password))


class NotifyLinkTest(Base):
    def row(self):
        return {"id": "s", "code": "BTC-20261107-A", "symbol": self.symbol, "bias_date": "2026-11-07", "direction": "long",
                "entry": 75480, "stop": 75090, "target": 76330, "zone_low": 75400, "zone_high": 75560,
                "trigger_type": "rejection", "state": {}, "expires_at": 0, "htf": None}

    def test_push_has_deep_link(self):
        link = server.WATCHER.link(self.row())
        self.assertEqual(link, "https://bamcp.online/setup-dashboard?symbol=BTCUSDT&date=2026-11-07&mode=live&tf=1h")
        alert = notifier.setup_alert(self.row(), {"event": "CANCELLED", "details": {"reason": "x"}}, server.TZ, link=link)
        self.assertTrue(alert.text.endswith(f"📊 Dashboard: {link}"))

    def test_no_link_keeps_text(self):
        alert = notifier.setup_alert(self.row(), {"event": "CANCELLED", "details": {"reason": "x"}}, server.TZ)
        self.assertNotIn("Dashboard", alert.text)

    def test_watcher_sends_link(self):
        sent = []

        async def deliver(cfg, alert, **kw):
            sent.append(alert)

        self.klines.upsert(self.symbol, "1h", real_h1_rows())
        codes = self.save_setups(SETUP_A)["codes"]
        setup = server.SETUPS.get(codes[0])
        ids = server.SETUPS.apply(setup, "CANCELLED", {}, [
            {"event": "CANCELLED", "to_status": "CANCELLED", "details": {"reason": "x"}}])
        with mock.patch.object(server.WATCHER, "deliver", deliver):
            run(server.WATCHER.send_event(ids[-1]))
        self.assertIn("setup-dashboard?symbol=BTCUSDT", sent[0].text)


class DashAuthUnitTest(unittest.TestCase):
    def test_password_rules(self):
        self.assertEqual(dashboard_auth.clean_username(" tan.mai_1 "), "tan.mai_1")
        for bad in ("ab", "a" * 33, "có dấu", "a b", "TanMai"):
            with self.assertRaises(ValueError):
                dashboard_auth.clean_username(bad)
        with self.assertRaises(ValueError):
            dashboard_auth.clean_password("x" * 9)
        with self.assertRaises(ValueError):
            dashboard_auth.clean_password("x" * 257)
        self.assertGreaterEqual(len(dashboard_auth.random_password()), 10)


if __name__ == "__main__":
    unittest.main()
