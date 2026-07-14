import sys, json
sys.path.insert(0, r"C:\Users\Elijah\FalconBroom")
from fbroom.workflow_rules import recipe_from_plain_english, infer_action, infer_columns_from_text
from fbroom.engine import Cleaner, _read_table
import logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

source = 'data/uploads/combined_whitespace_case_b7e41079.csv'
inst = 'normalize username'

# cleaner and profile
cleaner = Cleaner()
profile = cleaner.profile(source)
logger.info('PROFILE_KEYS: %s', list(profile.keys()))
logger.info('INFER_ACTION: %s', infer_action(inst))
logger.info('INFER_COLUMNS: %s', infer_columns_from_text(inst, profile, top_n=5))
# reconstruct preview cols
try:
    df_raw = _read_table(source)
    recon = Cleaner()._reconstruct_table_from_df(df_raw, offset=0, limit=1)
    recon_cols = recon[0].keys() if recon and isinstance(recon, list) and len(recon)>0 else []
except Exception as e:
    recon_cols = []
logger.info('RECON_COLS: %s', list(recon_cols))

recipe = recipe_from_plain_english(inst, profile, source, 'out.csv')
logger.info('\nRECIPE_JSON:')
try:
    rj = recipe.model_dump() if hasattr(recipe, 'model_dump') else recipe.dict()
    logger.info(json.dumps(rj, indent=2))
except Exception:
    logger.exception('RECIPE DUMP ERROR: %s', recipe)

# show preview using cleaner.preview_recipe
logger.info('\nPREVIEW:')
try:
    pv = cleaner.preview_recipe(recipe, n=5)
    logger.info(json.dumps(pv, indent=2, default=str))
except Exception as e:
    logger.exception('PREVIEW ERROR: %s', e)

# Direct test: apply _string_transform_column to raw table
from fbroom.engine import _read_table, _string_transform_column
logger.info('\nDIRECT TRANSFORM TEST:')
raw = _read_table(source)
logger.info('RAW_COLUMNS: %s', raw.columns)
try:
    after = _string_transform_column(raw, 'username', case='lower')
    # Polars vs pandas compatibility when printing head
    head = after.head(5)
    if hasattr(head, 'to_dicts'):
        logger.info('AFTER_HEAD: %s', head.to_dicts())
    else:
        try:
            logger.info('AFTER_HEAD: %s', head.to_dict('records'))
        except TypeError:
            try:
                logger.info('AFTER_HEAD: %s', head.to_dict())
            except Exception:
                try:
                    logger.info('AFTER_HEAD: %s', head.to_pandas().to_dict('records'))
                except Exception as e:
                    logger.exception('AFTER_HEAD ERROR: %s', e)
except Exception as e:
    logger.exception('DIRECT TRANSFORM ERROR: %s', e)
