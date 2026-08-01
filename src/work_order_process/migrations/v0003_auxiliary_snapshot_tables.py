"""Create the ERP and customer-account snapshot tables explicitly.

The exact UTF-8 DDL bytes are base64-encoded and frozen in this module. This
keeps non-ASCII SQL comments byte-stable while ensuring the migration checksum
covers the statements that will execute.
"""

from __future__ import annotations

import base64
import re
from typing import Any

VERSION = 3
NAME = "auxiliary_snapshot_tables"

_ERP_DATA_DDL_B64 = (
    "LS0gRVJQIOaVsOaNruihqO+8iOaWsOaXp0VSUOWQiOW5tuaVsOaNru+8iQ0KQ1JFQVRFIFRBQkxFIElGIE5PVCBF"
    "WElTVFMgZXJwX2RhdGEgKA0KICBpZCBCSUdJTlQgTk9UIE5VTEwgQVVUT19JTkNSRU1FTlQgQ09NTUVOVCAn6Ieq"
    "5aKe5Li76ZSuJywNCiAgc2VxX25vIElOVCBOVUxMIENPTU1FTlQgJ+WOn+Wni+ihjOWPtycsDQogIGNvbnRyYWN0"
    "X2lkIFZBUkNIQVIoNTApIE5PVCBOVUxMIENPTU1FTlQgJ+WQiOWQjOe8luWPtycsDQogIHNhbGVzX29yZyBWQVJD"
    "SEFSKDIwMCkgTlVMTCBDT01NRU5UICfplIDllK7nu4Tnu4cnLA0KICBpc19pbml0aWFsaXplZCBWQVJDSEFSKDEw"
    "KSBOVUxMIENPTU1FTlQgJ+aYr+WQpuWIneWni+WMlicsDQogIGNvbnRyYWN0X25hbWUgVkFSQ0hBUig1MDApIE5V"
    "TEwgQ09NTUVOVCAn5ZCI5ZCM5ZCN56ewJywNCiAgY29udHJhY3RfYXBwbHlfZGF0ZSBEQVRFIE5VTEwgQ09NTUVO"
    "VCAn5ZCI5ZCM55Sz6K+35pel5pyfJywNCiAgc2FsZXNfZGVwdCBWQVJDSEFSKDIwMCkgTlVMTCBDT01NRU5UICfp"
    "lIDllK7kuJrnu6npg6jpl6gnLA0KICBhcHBsaWNhbnQgVkFSQ0hBUig1MCkgTlVMTCBDT01NRU5UICfnlLPor7fk"
    "uronLA0KICBzYWxlc19wZXJzb24gVkFSQ0hBUig1MCkgTlVMTCBDT01NRU5UICfplIDllK7lkZgnLA0KICBzaWdu"
    "X2N1c3RvbWVyIFZBUkNIQVIoMjAwKSBOVUxMIENPTU1FTlQgJ+etvue6puWuouaItycsDQogIGZpbmFsX2N1c3Rv"
    "bWVyIFZBUkNIQVIoMjAwKSBOVUxMIENPTU1FTlQgJ+acgOe7iOWuouaItycsDQogIHRoaXJkX3BhcnR5IFZBUkNI"
    "QVIoMjAwKSBOVUxMIENPTU1FTlQgJ+esrOS4ieaWuScsDQogIGNvbnRyYWN0X3R5cGUgVkFSQ0hBUig1MCkgTlVM"
    "TCBDT01NRU5UICflkIjlkIznsbvlnosnLA0KICBpc19lc3RpbWF0ZWRfb3BzIFZBUkNIQVIoMTApIE5VTEwgQ09N"
    "TUVOVCAn5pqC5Lyw6L+Q57u06L+Q6JClJywNCiAgaXNfdmlydHVhbCBWQVJDSEFSKDEwKSBOVUxMIENPTU1FTlQg"
    "J+iZmuaLn+WQiOWQjCcsDQogIGlzXzIwMjZfc2Fhc19yZW5ldyBWQVJDSEFSKDEwKSBOVUxMIENPTU1FTlQgJzIw"
    "Mjbov5Dnu7RzYWFz57ut562+JywNCiAgZG9jX3N0YXR1cyBWQVJDSEFSKDUwKSBOVUxMIENPTU1FTlQgJ+WNleaN"
    "rueKtuaAgScsDQogIGNsb3NlX3N0YXR1cyBWQVJDSEFSKDUwKSBOVUxMIENPTU1FTlQgJ+WFs+mXreeKtuaAgScs"
    "DQogIGV4ZWNfc3RhdHVzIFZBUkNIQVIoNTApIE5VTEwgQ09NTUVOVCAn5ZCI5ZCM5omn6KGM54q25oCBJywNCiAg"
    "YXJjaGl2ZV9zdGF0dXMgVkFSQ0hBUig1MCkgTlVMTCBDT01NRU5UICflvZLmoaPnirbmgIEnLA0KICBhcmNoaXZl"
    "X2RhdGUgREFURSBOVUxMIENPTU1FTlQgJ+W9kuaho+aXpeacnycsDQogIHRvdGFsX2Ftb3VudCBERUNJTUFMKDE4"
    "LDIpIE5VTEwgQ09NTUVOVCAn5ZCI5ZCM5oC76YeR6aKdJywNCiAgZnJlZV9vcHNfbW9udGhzIElOVCBOVUxMIENP"
    "TU1FTlQgJ+WFjei0uei/kOe7tOacn++8iOaciO+8iScsDQogIGFubnVhbF9vcHNfYW1vdW50IERFQ0lNQUwoMTgs"
    "MikgTlVMTCBDT01NRU5UICflubTov5Dnu7Tnuqblrprph5Hpop0nLA0KICBjaXR5IFZBUkNIQVIoNTApIE5VTEwg"
    "Q09NTUVOVCAn5Z+O5biCJywNCiAgcHJvdmluY2UgVkFSQ0hBUig1MCkgTlVMTCBDT01NRU5UICfnnIHku70nLA0K"
    "ICBzYWxlc19jb250cmFjdF9kZXRhaWxfaWQgVkFSQ0hBUigxMDApIE5VTEwgQ09NTUVOVCAn6ZSA5ZSu5ZCI5ZCM"
    "5piO57uGaWQnLA0KICBpdGVtX2NvZGUgVkFSQ0hBUigxMDApIE5VTEwgQ09NTUVOVCAn5qCH55qE6KGM57yW56CB"
    "JywNCiAgaXRlbV9uYW1lIFZBUkNIQVIoNTAwKSBOVUxMIENPTU1FTlQgJ+agh+eahCcsDQogIGJ1c2luZXNzX3R5"
    "cGUgVkFSQ0hBUigxMDApIE5VTEwgQ09NTUVOVCAn5Lia5Yqh57G75Z6LJywNCiAgcHJvamVjdF9jb2RlIFZBUkNI"
    "QVIoMTAwKSBOVUxMIENPTU1FTlQgJ+S6pOS7mOmhueebrue8lueggScsDQogIHByb2plY3RfbmFtZSBWQVJDSEFS"
    "KDUwMCkgTlVMTCBDT01NRU5UICfkuqTku5jpobnnm64nLA0KICBvcHNfc2lnbl90eXBlIFZBUkNIQVIoMTAwKSBO"
    "VUxMIENPTU1FTlQgJ+i/kOe7tOetvue6puexu+WeiycsDQogIGRldGFpbF9xdHkgSU5UIE5VTEwgQ09NTUVOVCAn"
    "5piO57uG5pWw6YePJywNCiAgdW5pdF9wcmljZSBERUNJTUFMKDE4LDIpIE5VTEwgQ09NTUVOVCAn6ZSA5ZSu5Y2V"
    "5Lu3JywNCiAgZGV0YWlsX2Ftb3VudF93aXRoX3RheCBERUNJTUFMKDE4LDIpIE5VTEwgQ09NTUVOVCAn5piO57uG"
    "5Lu356iO5ZCI6K6hJywNCiAgb3BzX3N0YXJ0X2RhdGUgREFURSBOVUxMIENPTU1FTlQgJ+i/kOe7tOW8gOWni+aX"
    "peacnycsDQogIG9wc19lbmRfZGF0ZSBEQVRFIE5VTEwgQ09NTUVOVCAn6L+Q57u057uT5p2f5pel5pyfJywNCiAg"
    "ZXhlY19kZXRhaWxfaWQgVkFSQ0hBUigxMDApIE5VTEwgQ09NTUVOVCAn5omn6KGM5piO57uGaWQnLA0KICBwcm9k"
    "dWN0X21hdGVyaWFsIFZBUkNIQVIoNTAwKSBOVUxMIENPTU1FTlQgJ+S6p+WTgeeJqeaWmScsDQogIHByb2R1Y3Rf"
    "cmF0aW8gVkFSQ0hBUig1MCkgTlVMTCBDT01NRU5UICfkuqflk4HljaDmr5QnLA0KICBjbG91ZF9zZXJ2aWNlX3R5"
    "cGUgVkFSQ0hBUig1MCkgTlVMTCBDT01NRU5UICfkupHmnI3liqHnsbvlnosnLA0KICBwcm9kdWN0X2Ftb3VudCBE"
    "RUNJTUFMKDE4LDIpIE5VTEwgQ09NTUVOVCAn5Lqn5ZOB6YeR6aKdJywNCiAgcHJvZHVjdF9saW5lMSBWQVJDSEFS"
    "KDEwMCkgTlVMTCBDT01NRU5UICfkuIDnuqfkuqflk4Hnur8nLA0KICBwcm9kdWN0X2xpbmUyIFZBUkNIQVIoMTAw"
    "KSBOVUxMIENPTU1FTlQgJ+S6jOe6p+S6p+WTgee6vycsDQogIHByb2R1Y3RfY29tcGFueSBWQVJDSEFSKDIwMCkg"
    "TlVMTCBDT01NRU5UICfkuqflk4Hlhazlj7gnLA0KICBkaXZpc2lvbiBWQVJDSEFSKDEwMCkgTlVMTCBDT01NRU5U"
    "ICfmiYDlsZ7kuovkuJrpg6gnLA0KICBjdW1fYmlsbGluZyBERUNJTUFMKDE4LDIpIE5VTEwgQ09NTUVOVCAn57Sv"
    "6K6h5byA56Wo6YeR6aKdJywNCiAgY3VtX2NvbGxlY3Rpb24gREVDSU1BTCgxOCwyKSBOVUxMIENPTU1FTlQgJ+e0"
    "r+iuoeWbnuasvumHkeminScsDQogIGN1bV9yZXZlbnVlIERFQ0lNQUwoMTgsMikgTlVMTCBDT01NRU5UICfntK/o"
    "rqHnoa7mlLbph5Hpop0nLA0KICBjdXJfeWVhcl9iaWxsaW5nIERFQ0lNQUwoMTgsMikgTlVMTCBDT01NRU5UICfl"
    "vZPlubTlvIDnpajph5Hpop0nLA0KICBwcmV2X3llYXJfYmlsbGluZyBERUNJTUFMKDE4LDIpIE5VTEwgQ09NTUVO"
    "VCAn5Y675bm05ZCM5pyf5byA56Wo6YeR6aKdJywNCiAgY3VyX3llYXJfY29sbGVjdGlvbiBERUNJTUFMKDE4LDIp"
    "IE5VTEwgQ09NTUVOVCAn5b2T5bm05Zue5qy+6YeR6aKdJywNCiAgcHJldl95ZWFyX2NvbGxlY3Rpb24gREVDSU1B"
    "TCgxOCwyKSBOVUxMIENPTU1FTlQgJ+WOu+W5tOWQjOacn+WbnuasvumHkeminScsDQogIGN1cl95ZWFyX3JldmVu"
    "dWUgREVDSU1BTCgxOCwyKSBOVUxMIENPTU1FTlQgJ+W9k+W5tOaUtuWFpemHkeminScsDQogIHByZXZfeWVhcl9y"
    "ZXZlbnVlIERFQ0lNQUwoMTgsMikgTlVMTCBDT01NRU5UICfljrvlubTlkIzmnJ/mlLblhaXph5Hpop0nLA0KICBj"
    "dXJfeWVhcl9hbW9ydCBERUNJTUFMKDE4LDIpIE5VTEwgQ09NTUVOVCAn5b2T5bm05bqU5YiG5pGK6YeR6aKdJywN"
    "CiAgcHJldl95ZWFyX2Ftb3J0IERFQ0lNQUwoMTgsMikgTlVMTCBDT01NRU5UICfljrvlubTlkIzmnJ/lupTliIbm"
    "kYrph5Hpop0nLA0KICBzYWxlc19wbGF0Zm9ybSBWQVJDSEFSKDEwMCkgTlVMTCBDT01NRU5UICfokKXplIDlubPl"
    "j7AnLA0KICBzeXN0ZW1fZW5naW5lZXIgVkFSQ0hBUig1MCkgTlVMTCBDT01NRU5UICfkvZPns7vlt6XnqIvluIgn"
    "LA0KICBpc19wdWJsaWNfY2xvdWQgVkFSQ0hBUigxMCkgTlVMTCBDT01NRU5UICfmmK/lkKblhazmnInkupEnLA0K"
    "ICBpc19vbmVfdGltZV9yZXZlbnVlIFZBUkNIQVIoMTApIE5VTEwgQ09NTUVOVCAn5piv5ZCm5LiA5qyh5oCn5pS2"
    "5YWlJywNCiAgY29udHJhY3RfY2F0ZWdvcnkgVkFSQ0hBUig1MCkgTlVMTCBDT01NRU5UICflkIjlkIzliIbnsbsn"
    "LA0KICBidXNpbmVzc19jYXRlZ29yeSBWQVJDSEFSKDUwKSBOVUxMIENPTU1FTlQgJ+S4muWKoeexu+WIqycsDQog"
    "IG90aGVyX2J1c2luZXNzX3R5cGUgVkFSQ0hBUig1MCkgTlVMTCBDT01NRU5UICflhbbku5bkuJrliqHnsbvlnosn"
    "LA0KICBpbnZhbGlkX2NvbnRyYWN0X3R5cGUgVkFSQ0hBUig1MCkgTlVMTCBDT01NRU5UICfml6DmlYjlkIjlkIzn"
    "sbvlnosnLA0KICBkYXRhX3NvdXJjZSBWQVJDSEFSKDUwKSBOVUxMIENPTU1FTlQgJ+aVsOaNruadpea6kCcsDQog"
    "IGNyZWF0ZV9kYXRlIFZBUkNIQVIoOCkgTk9UIE5VTEwgQ09NTUVOVCAn5pWw5o2u5pel5pyfJywNCiAgY29udHJh"
    "Y3RfZGF5cyBJTlQgTlVMTCBDT01NRU5UICflkIjlkIzlpKnmlbAnLA0KICBwcmV2X3llYXJfcGVyaW9kX3N0YXJ0"
    "IERBVEUgTlVMTCBDT01NRU5UICfljrvlubTnu5/orqHotbflp4vml6XmnJ8nLA0KICBwcmV2X3llYXJfcGVyaW9k"
    "X2VuZCBEQVRFIE5VTEwgQ09NTUVOVCAn5Y675bm057uf6K6h5oiq5q2i5pel5pyfJywNCiAgcHJldl95ZWFyX2Nh"
    "bGNfYW1vcnQgREVDSU1BTCgxOCwyKSBOVUxMIENPTU1FTlQgJ+WOu+W5tOaMieacn+WIhuaRiuacjeWKoei0uScs"
    "DQogIHByZXZfeWVhcl9hZGp1c3RlZF9hbW9ydCBERUNJTUFMKDE4LDIpIE5VTEwgQ09NTUVOVCAn5Y675bm05YCS"
    "562+6LCD5pW05ZCO5YiG5pGK5pyN5Yqh6LS5JywNCiAgY3VyX3llYXJfcGVyaW9kX3N0YXJ0IERBVEUgTlVMTCBD"
    "T01NRU5UICfku4rlubTnu5/orqHotbflp4vml6XmnJ8nLA0KICBjdXJfeWVhcl9wZXJpb2RfZW5kIERBVEUgTlVM"
    "TCBDT01NRU5UICfku4rlubTnu5/orqHmiKrmraLml6XmnJ8nLA0KICBjdXJfeWVhcl9jYWxjX2Ftb3J0IERFQ0lN"
    "QUwoMTgsMikgTlVMTCBDT01NRU5UICfku4rlubTmjInmnJ/liIbmkYrmnI3liqHotLknLA0KICBjdXJfeWVhcl9h"
    "ZGp1c3RlZF9hbW9ydCBERUNJTUFMKDE4LDIpIE5VTEwgQ09NTUVOVCAn5LuK5bm05YCS562+6LCD5pW05ZCO5YiG"
    "5pGK5pyN5Yqh6LS5JywNCiAgZmlsZV9zb3VyY2VfZGF0ZSBWQVJDSEFSKDIwKSBOVUxMIENPTU1FTlQgJ+aWh+S7"
    "tuadpea6kOaXpeacnycsDQogIGltcG9ydGVkX2F0IFRJTUVTVEFNUCBOT1QgTlVMTCBERUZBVUxUIENVUlJFTlRf"
    "VElNRVNUQU1QIENPTU1FTlQgJ+WFpeW6k+aXtumXtCcsDQogIFBSSU1BUlkgS0VZIChpZCwgY3JlYXRlX2RhdGUp"
    "LA0KICBVTklRVUUgS0VZIHVrX3NuYXBzaG90X2xpbmUgKGNvbnRyYWN0X2lkLCBpdGVtX2NvZGUsIGV4ZWNfZGV0"
    "YWlsX2lkLCBjcmVhdGVfZGF0ZSksDQogIElOREVYIGlkeF9wcm92aW5jZV9jaXR5IChwcm92aW5jZSwgY2l0eSks"
    "DQogIElOREVYIGlkeF9jb250cmFjdF90eXBlIChjb250cmFjdF90eXBlKSwNCiAgSU5ERVggaWR4X3NhbGVzX2Rl"
    "cHQgKHNhbGVzX2RlcHQpLA0KICBJTkRFWCBpZHhfcHJvZHVjdF9saW5lIChwcm9kdWN0X2xpbmUxLCBwcm9kdWN0"
    "X2xpbmUyKSwNCiAgSU5ERVggaWR4X2RhdGFfc291cmNlIChkYXRhX3NvdXJjZSksDQogIElOREVYIGlkeF9jcmVh"
    "dGVfZGF0ZSAoY3JlYXRlX2RhdGUpLA0KICBJTkRFWCBpZHhfY29udHJhY3RfZGF0ZSAoY29udHJhY3RfYXBwbHlf"
    "ZGF0ZSksDQogIElOREVYIGlkeF9kb2Nfc3RhdHVzIChkb2Nfc3RhdHVzKSwNCiAgSU5ERVggaWR4X2ZpbmFsX2N1"
    "c3RvbWVyIChmaW5hbF9jdXN0b21lcikNCikgRU5HSU5FPUlubm9EQiBERUZBVUxUIENIQVJTRVQ9dXRmOG1iNCBD"
    "T0xMQVRFPXV0ZjhtYjRfdW5pY29kZV9jaSBDT01NRU5UPSdFUlDlkIjlkIzmlbDmja4nDQpQQVJUSVRJT04gQlkg"
    "UkFOR0UgQ09MVU1OUyhjcmVhdGVfZGF0ZSkgKA0KICBQQVJUSVRJT04gcF9mdXR1cmUgVkFMVUVTIExFU1MgVEhB"
    "TiAoTUFYVkFMVUUpDQopOw0K"
)
_CUSTOMER_ACCOUNT_DDL_B64 = (
    "LS0gQ3VzdG9tZXIgYWNjb3VudCBzbmFwc2hvdCB0YWJsZS4gT25lIGltcG9ydCBkYXRlIHJlcHJlc2VudHMgb25l"
    "IHNvdXJjZSBzbmFwc2hvdC4NCkNSRUFURSBUQUJMRSBJRiBOT1QgRVhJU1RTIGN1c3RvbWVyX2FjY291bnQgKA0K"
    "ICBpZCBCSUdJTlQgTk9UIE5VTEwgQVVUT19JTkNSRU1FTlQsDQogIG1hcmtldGluZ19wbGF0Zm9ybSBWQVJDSEFS"
    "KDEwMCkgTlVMTCwNCiAgY29udHJhY3Rfc2lnbl9jdXN0b21lciBWQVJDSEFSKDIwMCkgTlVMTCwNCiAgZmluYWxf"
    "dXNlcl9jdXN0b21lciBWQVJDSEFSKDIwMCkgTlVMTCwNCiAgYW5udWFsX29wc19mZWUgREVDSU1BTCgxOCwyKSBO"
    "VUxMLA0KICBidXNpbmVzc19jYXRlZ29yeSBWQVJDSEFSKDUwKSBOVUxMLA0KICBpc19pbl90YXJnZXQgVkFSQ0hB"
    "UigxMCkgTlVMTCwNCiAgc2VydmljZV9leHBpcmVfZGF0ZSBEQVRFIE5VTEwsDQogIHNpZ25fcHJvZ3Jlc3MgVkFS"
    "Q0hBUig1MCkgTlVMTCwNCiAgY29udHJhY3RfY29kZSBWQVJDSEFSKDEwMCkgTlVMTCwNCiAgaXRlbV9jb2RlIFZB"
    "UkNIQVIoMTAwKSBOVUxMLA0KICBwcm92aW5jZSBWQVJDSEFSKDUwKSBOVUxMLA0KICBjaXR5IFZBUkNIQVIoNTAp"
    "IE5VTEwsDQogIGRpc3RyaWN0IFZBUkNIQVIoNTApIE5VTEwsDQogIG9wc19pdGVtIFZBUkNIQVIoMTAwKSBOVUxM"
    "LA0KICBlbnZfcHJvamVjdF9uYW1lIFZBUkNIQVIoMjAwKSBOVUxMLA0KICBjdXN0b21lcl90eXBlIFZBUkNIQVIo"
    "NTApIE5VTEwsDQogIHNhbGVzX2RlcHQgVkFSQ0hBUigyMDApIE5VTEwsDQogIHNhbGVzX3BlcnNvbiBWQVJDSEFS"
    "KDUwKSBOVUxMLA0KICB1bnNpZ25lZF9jYXRlZ29yeSBWQVJDSEFSKDUwKSBOVUxMLA0KICBleGNsdWRlX3JlYXNv"
    "bl9jYXRlZ29yeSBWQVJDSEFSKDUwKSBOVUxMLA0KICBleGNsdWRlX3JlYXNvbl9kZXNjIFZBUkNIQVIoNTAwKSBO"
    "VUxMLA0KICBjb250cmFjdF9uYW1lIFZBUkNIQVIoNTAwKSBOVUxMLA0KICBjb250cmFjdF9hcHBseV9kYXRlIERB"
    "VEUgTlVMTCwNCiAgY29udHJhY3RfdHlwZSBWQVJDSEFSKDUwKSBOVUxMLA0KICBhcmNoaXZlX3N0YXR1cyBWQVJD"
    "SEFSKDUwKSBOVUxMLA0KICBpc192aXJ0dWFsIFZBUkNIQVIoMTApIE5VTEwsDQogIG9wc19zdGFydF9kYXRlIERB"
    "VEUgTlVMTCwNCiAgb3BzX2VuZF9kYXRlIERBVEUgTlVMTCwNCiAgZGV0YWlsX2Ftb3VudCBERUNJTUFMKDE4LDIp"
    "IE5VTEwsDQogIGV4cGVjdGVkX3JldmVudWUgREVDSU1BTCgxOCwyKSBOVUxMLA0KICBleHBlY3RlZF9jb2xsZWN0"
    "aW9uIERFQ0lNQUwoMTgsMikgTlVMTCwNCiAgYWN0dWFsX3JldmVudWUgREVDSU1BTCgxOCwyKSBOVUxMLA0KICBh"
    "Y3R1YWxfY29sbGVjdGlvbiBERUNJTUFMKDE4LDIpIE5VTEwsDQogIGFjY2VwdGFuY2VfZGF0ZSBEQVRFIE5VTEws"
    "DQogIGNvbnRyYWN0X2NvdW50IElOVCBOVUxMLA0KICBwYXltZW50X21ldGhvZCBWQVJDSEFSKDUwKSBOVUxMLA0K"
    "ICBjb250YWN0X3BlcnNvbiBWQVJDSEFSKDUwKSBOVUxMLA0KICBjb250YWN0X3Bob25lIFZBUkNIQVIoMTAwKSBO"
    "VUxMLA0KICBjb21tdW5pY2F0aW9uX2RldGFpbCBWQVJDSEFSKDUwMCkgTlVMTCwNCiAgcmVtYXJrIFZBUkNIQVIo"
    "NTAwKSBOVUxMLA0KICBjcmVhdGVfZGF0ZSBWQVJDSEFSKDgpIE5PVCBOVUxMLA0KICBpbXBvcnRlZF9hdCBUSU1F"
    "U1RBTVAgTk9UIE5VTEwgREVGQVVMVCBDVVJSRU5UX1RJTUVTVEFNUCwNCiAgUFJJTUFSWSBLRVkgKGlkLCBjcmVh"
    "dGVfZGF0ZSksDQogIEtFWSBpZHhfY29udHJhY3RfY29kZSAoY29udHJhY3RfY29kZSksDQogIEtFWSBpZHhfaXRl"
    "bV9jb2RlIChpdGVtX2NvZGUpLA0KICBLRVkgaWR4X3NpZ25fY3VzdG9tZXIgKGNvbnRyYWN0X3NpZ25fY3VzdG9t"
    "ZXIpLA0KICBLRVkgaWR4X2ZpbmFsX3VzZXIgKGZpbmFsX3VzZXJfY3VzdG9tZXIpLA0KICBLRVkgaWR4X3Byb3Zp"
    "bmNlX2NpdHkgKHByb3ZpbmNlLCBjaXR5KSwNCiAgS0VZIGlkeF9jcmVhdGVfZGF0ZSAoY3JlYXRlX2RhdGUpLA0K"
    "ICBLRVkgaWR4X3NlcnZpY2VfZXhwaXJlIChzZXJ2aWNlX2V4cGlyZV9kYXRlKSwNCiAgS0VZIGlkeF9zYWxlc19k"
    "ZXB0IChzYWxlc19kZXB0KQ0KKSBFTkdJTkU9SW5ub0RCIERFRkFVTFQgQ0hBUlNFVD11dGY4bWI0IENPTExBVEU9"
    "dXRmOG1iNF91bmljb2RlX2NpDQpQQVJUSVRJT04gQlkgUkFOR0UgQ09MVU1OUyhjcmVhdGVfZGF0ZSkgKA0KICBQ"
    "QVJUSVRJT04gcF9mdXR1cmUgVkFMVUVTIExFU1MgVEhBTiAoTUFYVkFMVUUpDQopOw0K"
)

_ERP_DATA_DDL = base64.b64decode(_ERP_DATA_DDL_B64).decode("utf-8")
_CUSTOMER_ACCOUNT_DDL = base64.b64decode(_CUSTOMER_ACCOUNT_DDL_B64).decode("utf-8")
_TABLE_DDLS = {
    "erp_data": _ERP_DATA_DDL,
    "customer_account": _CUSTOMER_ACCOUNT_DDL,
}


def _required_columns(statement: str) -> frozenset[str]:
    ignored = {"PRIMARY", "UNIQUE", "KEY", "INDEX", "PARTITION"}
    columns: set[str] = set()
    for line in statement.splitlines():
        match = re.match(r"\s{2}`?([A-Za-z_][A-Za-z0-9_]*)`?\s+", line)
        if match and match.group(1).upper() not in ignored:
            columns.add(match.group(1))
    return frozenset(columns)


_REQUIRED_COLUMNS = {
    table: _required_columns(statement) for table, statement in _TABLE_DDLS.items()
}


def _required_indexes(statement: str) -> dict[str, tuple[int, tuple[str, ...]]]:
    indexes: dict[str, tuple[int, tuple[str, ...]]] = {}
    for line in statement.splitlines():
        match = re.match(
            r"\s{2}(PRIMARY KEY|UNIQUE KEY|KEY|INDEX)"
            r"(?:\s+`?([A-Za-z_][A-Za-z0-9_]*)`?)?\s*\(([^)]+)\)",
            line,
            re.IGNORECASE,
        )
        if match is None:
            continue
        kind, name, raw_columns = match.groups()
        index_name = "PRIMARY" if kind.upper() == "PRIMARY KEY" else str(name)
        non_unique = 0 if kind.upper() in {"PRIMARY KEY", "UNIQUE KEY"} else 1
        columns = tuple(
            value.strip().strip("`").split("(", maxsplit=1)[0] for value in raw_columns.split(",")
        )
        indexes[index_name] = (non_unique, columns)
    return indexes


_REQUIRED_INDEXES = {
    table: _required_indexes(statement) for table, statement in _TABLE_DDLS.items()
}


def _existing_columns(cursor: Any, database: str, table: str) -> set[str]:
    cursor.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s",
        (database, table),
    )
    return {str(row[0]) for row in cursor.fetchall()}


def _existing_indexes(
    cursor: Any,
    database: str,
    table: str,
) -> dict[str, tuple[int, tuple[str, ...]]]:
    cursor.execute(
        "SELECT index_name, non_unique, seq_in_index, column_name "
        "FROM information_schema.statistics "
        "WHERE table_schema = %s AND table_name = %s "
        "ORDER BY index_name, seq_in_index",
        (database, table),
    )
    grouped: dict[str, tuple[int, list[tuple[int, str]]]] = {}
    for name, non_unique, sequence, column in cursor.fetchall():
        index_name = str(name)
        if index_name not in grouped:
            grouped[index_name] = (int(non_unique), [])
        grouped[index_name][1].append((int(sequence), str(column)))
    return {
        name: (
            non_unique,
            tuple(column for _, column in sorted(columns)),
        )
        for name, (non_unique, columns) in grouped.items()
    }


def _partitions(
    cursor: Any,
    database: str,
    table: str,
) -> dict[str, tuple[str, str, str]]:
    cursor.execute(
        "SELECT partition_name, partition_description, "
        "partition_method, partition_expression "
        "FROM information_schema.partitions "
        "WHERE table_schema = %s AND table_name = %s "
        "ORDER BY partition_ordinal_position",
        (database, table),
    )
    partitions: dict[str, tuple[str, str, str]] = {}
    for name, description, method, expression in cursor.fetchall():
        if name is None:
            continue
        normalized_method = re.sub(r"\s+", " ", str(method or "").strip()).upper()
        normalized_expression = re.sub(r"[\s`]", "", str(expression or "")).lower()
        while normalized_expression.startswith("(") and normalized_expression.endswith(")"):
            normalized_expression = normalized_expression[1:-1]
        partitions[str(name)] = (
            str(description or "").strip(" '\"`").upper(),
            normalized_method,
            normalized_expression,
        )
    return partitions


def _structure_issues(cursor: Any, database: str, table: str) -> list[str]:
    actual_indexes = _existing_indexes(cursor, database, table)
    issues = [
        f"index {name} expected={signature} actual={actual_indexes.get(name)}"
        for name, signature in _REQUIRED_INDEXES[table].items()
        if actual_indexes.get(name) != signature
    ]
    future_partition = _partitions(cursor, database, table).get("p_future")
    if future_partition is None or future_partition[0] != "MAXVALUE":
        issues.append("partition p_future must use MAXVALUE")
    if future_partition is not None and future_partition[1:] != (
        "RANGE",
        "create_date",
    ):
        issues.append("partitioning must use RANGE COLUMNS(create_date)")
    return issues


def is_satisfied(cursor: Any, database: str) -> bool:
    """Check frozen columns, functional indexes, and the MAXVALUE partition."""

    for table, required in _REQUIRED_COLUMNS.items():
        if not required.issubset(_existing_columns(cursor, database, table)):
            return False
        if _structure_issues(cursor, database, table):
            return False
    return True


def apply(cursor: Any, database: str) -> None:
    """Create missing tables and reject ambiguous partial legacy structures."""

    for table, statement in _TABLE_DDLS.items():
        existing = _existing_columns(cursor, database, table)
        required = _REQUIRED_COLUMNS[table]
        if not existing:
            cursor.execute(statement)
            continue
        missing = sorted(required - existing)
        if missing:
            raise RuntimeError(
                f"{table} is missing required columns {missing}; manual repair is required"
            )
        issues = _structure_issues(cursor, database, table)
        if issues:
            raise RuntimeError(
                f"{table} has incompatible functional structure: "
                f"{'; '.join(issues)}; manual repair is required"
            )
