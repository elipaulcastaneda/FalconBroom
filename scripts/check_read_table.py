import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from fbroom.engine import _read_table, _is_polars_df
paths = [
    r"C:\Users\Elijah\FalconBroom\data\uploads\orders_c4b5eb0e_10472_d01c.csv",
    r"C:\Users\Elijah\FalconBroom\data\uploads\customers_0cfcb5cf_17110_92c.csv",
]
for p in paths:
    print('PATH:', p)
    try:
        df = _read_table(p)
        print('TYPE:', type(df))
        try:
            cols = list(df.columns if _is_polars_df(df) else list(df.columns))
        except Exception as e:
            cols = f'error getting columns: {e}'
        print('COLUMNS:', cols)
        # print head
        try:
            if _is_polars_df(df):
                print('HEAD:', df.head(3).to_dicts())
            else:
                print('HEAD:', df.head(3).to_dict())
        except Exception as e:
            print('head error', e)
    except Exception as e:
        print('ERROR reading', e)
    print('---')

    # Try local reconstruction logic same as in main.join_preview
    print('\nAttempting local reconstruction...')
    try:
        import csv as _csv, io as _io
        try:
            import polars as pl
        except Exception:
            pl = None
        def _local_reconstruct(df):
            if not (hasattr(df, 'columns') and {'unit_kind', 'text'}.issubset(set(df.columns if hasattr(df, 'columns') else []))):
                return df
            if _is_polars_df(df):
                lines = df.filter(pl.col('unit_kind') == 'line').sort('row_index').select(['row_index', 'text']).to_dicts()
            else:
                lines = df[df.get('unit_kind') == 'line'][['row_index', 'text']].to_dict('records')
            print('found line units:', len(lines))
            if not lines:
                return df
            sample = '\n'.join(str(r.get('text', '')) for r in lines[:8])
            counts = {',': sample.count(','), '\t': sample.count('\t'), ';': sample.count(';'), '|': sample.count('|')}
            delim = max(counts.items(), key=lambda kv: kv[1])[0]
            print('detected delim:', delim)
            parsed = []
            for r in lines:
                text = r.get('text') or ''
                reader = _csv.reader(_io.StringIO(text), delimiter=delim)
                row = next(reader, [])
                if row:
                    parsed.append(row)
            print('parsed rows:', len(parsed))
            if not parsed:
                return df
            header = parsed[0]
            data_rows = parsed[1:]
            print('header:', header)
            if not data_rows:
                cols = [f'col{i}' for i in range(len(header))]
                data = [dict(zip(cols, header))]
            else:
                cols = header
                data = [dict(zip(cols, r)) for r in data_rows]
            try:
                return pl.DataFrame(data) if pl is not None else __import__('pandas').DataFrame(data)
            except Exception:
                import pandas as _pd
                return _pd.DataFrame(data)
        for p in paths:
            print('RECONSTRUCT FOR', p)
            df = _read_table(p)
            recon = _local_reconstruct(df)
            try:
                print('recon cols:', list(recon.columns))
                print('recon head:', recon.head(3).to_dicts() if _is_polars_df(recon) else recon.head(3).to_dict())
            except Exception as e:
                print('recon error', e)
    except Exception as e:
        print('reconstruction error', e)
