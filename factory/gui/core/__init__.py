# Core infrastructure layer (L2). Pure Python plus the runtime; no lvgl, no flow.
#
# Deliberately empty: importing this package must not read /data, load a kmodel
# or start a timer. Import the submodule you need:
#
#   from core import config
#   from core.errors import AppUnavailable
#
# Contents: paths, errors, config, i18n, version, font_pool, model_pool,
# ocr_dict. `paths` is the single asset-path resolver for every layer above.
