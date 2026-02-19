"""
Main Streamlit application for the STIBO vs SAP reconciliation dashboard.
"""

import streamlit as st
import polars as pl
import pandas as pd
from pathlib import Path
import logging
import json
import subprocess
import sys

# Allow Pandas Styler to render large comparison tables (default 262144 cells)
pd.set_option("styler.render.max_elements", 1_000_000)

from config import ATTRIBUTE_CONFIG, CONTEXT_COLUMNS, JOIN_KEY_STIBO, JOIN_KEY_SAP
from loaders import load_and_prepare_data, load_excel_file
from comparison_engine import build_comparison_table, get_comparison_statistics
from mapping_loader import (
    load_column_mapping,
    detect_columns,
    build_attribute_config_from_mapping
)
from inner_outer_analysis import (
    build_inner_outer_non_generic,
    build_inner_outer_export_rows,
    export_inner_outer_to_excel_bytes,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

st.set_page_config(
    page_title="STIBO vs SAP Reconciliation",
    layout="wide"
)

# Initialisation de la session state
if 'comparisons_df' not in st.session_state:
    st.session_state.comparisons_df = None
if 'stats' not in st.session_state:
    st.session_state.stats = None
if 'attribute_config' not in st.session_state:
    st.session_state.attribute_config = None
if 'context_columns' not in st.session_state:
    st.session_state.context_columns = None
if 'join_key' not in st.session_state:
    st.session_state.join_key = None
if 'df_elist' not in st.session_state:
    st.session_state.df_elist = None
if 'df_current_range' not in st.session_state:
    st.session_state.df_current_range = None
if 'df_stibo' not in st.session_state:
    st.session_state.df_stibo = None
if 'inner_outer_export_df' not in st.session_state:
    st.session_state.inner_outer_export_df = None

if 'loaded_from_precomputed' not in st.session_state:
    st.session_state.loaded_from_precomputed = False

if 'comparison_wide' not in st.session_state:
    st.session_state.comparison_wide = None

if 'stibo_only' not in st.session_state:
    st.session_state.stibo_only = None

if 'sap_only' not in st.session_state:
    st.session_state.sap_only = None


def load_from_precomputed(output_dir: str = "output") -> bool:
    """Load precomputed comparison table from output/ directory."""
    try:
        output_path = Path(output_dir)
        comparison_path = output_path / 'comparison.parquet'
        stats_path = output_path / 'stats.json'
        
        if not comparison_path.exists():
            st.warning(f"Precomputed comparison table not found at {comparison_path}")
            return False
        
        with st.spinner("Loading precomputed data..."):
            # Load comparison table (wide format)
            df_comparison_wide = pl.read_parquet(comparison_path)
            st.session_state.comparison_wide = df_comparison_wide
            
            # Load stats
            if stats_path.exists():
                with open(stats_path, 'r', encoding='utf-8') as f:
                    stats = json.load(f)
                st.session_state.stats = stats
            else:
                st.warning("Stats file not found, using default stats")
                st.session_state.stats = {}
            
            # Load STIBO_only and SAP_only if they exist
            stibo_only_path = output_path / 'stibo_only.parquet'
            sap_only_path = output_path / 'sap_only.parquet'
            
            if stibo_only_path.exists():
                st.session_state.stibo_only = pl.read_parquet(stibo_only_path)
            else:
                st.session_state.stibo_only = None
            
            if sap_only_path.exists():
                st.session_state.sap_only = pl.read_parquet(sap_only_path)
            else:
                st.session_state.sap_only = None
            
            # Load Inner=Outer if exists
            inner_outer_path = output_path / 'inner_outer.parquet'
            if inner_outer_path.exists():
                st.session_state.inner_outer_export_df = pl.read_parquet(inner_outer_path)
            else:
                st.session_state.inner_outer_export_df = None
            
            st.session_state.loaded_from_precomputed = True
            return True
            
    except Exception as e:
        st.error(f"Error loading precomputed data: {e}")
        logger.exception("Details:")
        return False


def run_build_comparison(excel_file: str, market: str = "Brakes", output_dir: str = "output") -> bool:
    """Run build_comparison.py script and return success status."""
    try:
        script_path = Path(__file__).parent / "build_comparison.py"
        if not script_path.exists():
            st.error(f"build_comparison.py not found at {script_path}")
            return False
        
        excel_path = Path(excel_file)
        if not excel_path.exists():
            st.error(f"Excel file not found: {excel_file}")
            return False
        
        with st.spinner(f"Running build_comparison.py for {excel_file}..."):
            # Run the script
            result = subprocess.run(
                [sys.executable, str(script_path), str(excel_path), "--market", market, "--output-dir", output_dir],
                capture_output=True,
                text=True,
                cwd=Path(__file__).parent
            )
            
            if result.returncode == 0:
                st.success("Precomputation completed successfully!")
                logger.info("Build comparison output:")
                logger.info(result.stdout)
                return True
            else:
                st.error(f"Precomputation failed:\n{result.stderr}")
                logger.error(f"Build comparison error:\n{result.stderr}")
                return False
                
    except Exception as e:
        st.error(f"Error running build_comparison.py: {e}")
        logger.exception("Details:")
        return False


def load_data():
    """Load and prepare data with automatic column detection."""
    try:
        excel_file = "STIBO Brakes Product Full Extract - 09.01.26 - sent Ilyass.xlsx"
        stibo_sheet = "STIBO Seed Extract"
        sap_sheet = "SAP extract Helen"
        elist_sheet = "Elist"
        current_range_sheet = "Current Range"
        
        if not Path(excel_file).exists():
            st.error(f"File not found: {excel_file}")
            return False
        
        with st.spinner("Loading data..."):
            # Read headers first to detect columns (much faster)
            from loaders import get_excel_columns
            stibo_headers, stibo_name_mapping = get_excel_columns(excel_file, sheet_name=stibo_sheet, header_row=0)
            sap_headers, sap_name_mapping = get_excel_columns(excel_file, sheet_name=sap_sheet, header_row=1)
            
            # Load mapping and detect which columns we need
            mapping = load_column_mapping()
            market = st.session_state.get('selected_market', 'Brakes')
            join_keys, attribute_columns, context_columns = detect_columns(
                mapping,
                stibo_headers,
                sap_headers,
                market=market
            )
            
            # Build list of columns to load for STIBO (using cleaned names)
            stibo_cols_cleaned = [join_keys['stibo']]
            for attr_cols in attribute_columns.values():
                if attr_cols.get('stibo'):
                    stibo_cols_cleaned.append(attr_cols['stibo'])
            for ctx_col in context_columns.get('stibo', {}).values():
                if ctx_col:
                    stibo_cols_cleaned.append(ctx_col)
            # Add columns needed for Inner=Outer analysis
            from inner_outer_analysis import COL_GTIN_INNER, COL_GTIN_OUTER, COL_LEGAL
            for inner_outer_col in [COL_GTIN_INNER, COL_GTIN_OUTER, COL_LEGAL]:
                # Check if column exists in headers (case-insensitive)
                for h in stibo_headers:
                    if h.lower() == inner_outer_col.lower():
                        stibo_cols_cleaned.append(h)
                        break
            stibo_cols_cleaned = list(set(stibo_cols_cleaned))  # Remove duplicates
            
            # Map cleaned names back to original names for pandas usecols
            stibo_cols_to_load = [stibo_name_mapping.get(col, col) for col in stibo_cols_cleaned if col in stibo_name_mapping]
            if not stibo_cols_to_load:
                logger.warning("No STIBO columns to load, loading all columns")
                stibo_cols_to_load = None
            
            # Build list of columns to load for SAP (using cleaned names)
            sap_cols_cleaned = [join_keys['sap']]
            for attr_cols in attribute_columns.values():
                if attr_cols.get('sap'):
                    sap_cols_cleaned.append(attr_cols['sap'])
            for ctx_col in context_columns.get('sap', {}).values():
                if ctx_col:
                    sap_cols_cleaned.append(ctx_col)
            sap_cols_cleaned = list(set(sap_cols_cleaned))  # Remove duplicates
            
            # Map cleaned names back to original names for pandas usecols
            sap_cols_to_load = [sap_name_mapping.get(col, col) for col in sap_cols_cleaned if col in sap_name_mapping]
            if not sap_cols_to_load:
                logger.warning("No SAP columns to load, loading all columns")
                sap_cols_to_load = None
            
            # Load only necessary columns (or all if detection failed)
            df_stibo_temp = load_excel_file(excel_file, sheet_name=stibo_sheet, header_row=0, usecols=stibo_cols_to_load)
            df_sap_temp = load_excel_file(excel_file, sheet_name=sap_sheet, header_row=1, usecols=sap_cols_to_load)
            
            st.session_state.df_stibo_raw = df_stibo_temp
            st.session_state.df_sap_raw = df_sap_temp
            
            with st.expander("Detected columns", expanded=False):
                st.write("**Join keys:**")
                st.write(f"- STIBO: `{join_keys['stibo']}`")
                st.write(f"- SAP: `{join_keys['sap']}`")
                st.write("\n**Detected attributes:**")
                for attr, cols in attribute_columns.items():
                    st.write(f"- {attr}: STIBO=`{cols.get('stibo', 'N/A')}`, SAP=`{cols.get('sap', 'N/A')}`")
            
            attribute_config = build_attribute_config_from_mapping(attribute_columns, {})
            
            if not attribute_config:
                st.warning("No attribute detected. Check column_mapping.json")
                return False
            
            # Prepare data using already loaded DataFrames (no reload needed)
            df_stibo, df_sap, df_elist, df_current_range = load_and_prepare_data(
                excel_file,
                stibo_sheet,
                sap_sheet,
                join_keys['stibo'],
                join_keys['sap'],
                elist_sheet=elist_sheet,
                current_range_sheet=current_range_sheet,
                stibo_header_row=0,
                sap_header_row=1,
                df_stibo_preloaded=df_stibo_temp,
                df_sap_preloaded=df_sap_temp
            )
            
            st.session_state.df_stibo = df_stibo
            st.session_state.df_sap = df_sap
            st.session_state.df_elist = df_elist
            st.session_state.df_current_range = df_current_range
            st.session_state.join_key = join_keys['stibo']  # Clé unifiée
            st.session_state.attribute_config = attribute_config
            st.session_state.context_columns = context_columns
        
        with st.spinner("Building comparison table..."):
            comparisons_df = build_comparison_table(
                df_stibo,
                df_sap,
                st.session_state.attribute_config,
                st.session_state.context_columns,
                st.session_state.join_key,
                df_elist=df_elist,
                df_current_range=df_current_range
            )
            
            st.session_state.comparisons_df = comparisons_df
            st.session_state.stats = get_comparison_statistics(comparisons_df)
        
        return True
    except Exception as e:
        st.error(f"Load error: {e}")
        logger.exception("Details:")
        
        if 'df_stibo_temp' in locals() or 'df_stibo_raw' in st.session_state:
            with st.expander("Columns available for debugging"):
                col1, col2 = st.columns(2)
                with col1:
                    st.write("**STIBO:**")
                    df_stibo_debug = st.session_state.get('df_stibo_raw', df_stibo_temp if 'df_stibo_temp' in locals() else None)
                    if df_stibo_debug is not None:
                        # Polars columns est déjà une liste, pas besoin de .to_list()
                        cols = df_stibo_debug.columns
                        st.write(cols if isinstance(cols, list) else list(cols))
                with col2:
                    st.write("**SAP:**")
                    df_sap_debug = st.session_state.get('df_sap_raw', df_sap_temp if 'df_sap_temp' in locals() else None)
                    if df_sap_debug is not None:
                        # Polars columns est déjà une liste, pas besoin de .to_list()
                        cols = df_sap_debug.columns
                        st.write(cols if isinstance(cols, list) else list(cols))
        
        return False


def _pct_skus_complete_from_wide(df_wide: pl.DataFrame) -> tuple[float, int, int]:
    """% of SKUs where all attributes are MATCH. Returns (pct, complete_count, total_skus)."""
    check_cols = [c for c in df_wide.columns if c.endswith(' Check')]
    if not check_cols or len(df_wide) == 0:
        return 0.0, 0, len(df_wide)
    total = len(df_wide)
    all_match = pl.lit(True)
    for col in check_cols:
        all_match = all_match & (pl.col(col) == 'MATCH')
    complete_count = df_wide.filter(all_match).height
    pct = (complete_count / total * 100) if total else 0.0
    return round(pct, 1), complete_count, total


def _match_and_valid_pct_by_attribute_from_wide(df_wide: pl.DataFrame) -> pl.DataFrame:
    """Per attribute: % match (MATCH) and % valid (non-empty = MATCH or MISMATCH)."""
    check_cols = [c for c in df_wide.columns if c.endswith(' Check')]
    if not check_cols:
        return pl.DataFrame()
    rows = []
    total_skus = len(df_wide)
    for col in check_cols:
        attr_name = col.replace(' Check', '')
        match_count = (df_wide[col] == 'MATCH').sum()
        valid_count = ((df_wide[col] == 'MATCH') | (df_wide[col] == 'MISMATCH')).sum()
        match_pct = (match_count / total_skus * 100) if total_skus else 0.0
        valid_pct = (valid_count / total_skus * 100) if total_skus else 0.0
        rows.append({
            'attribute': attr_name,
            'match_pct': round(match_pct, 1),
            'valid_pct': round(valid_pct, 1),
        })
    return pl.DataFrame(rows).sort('match_pct', descending=False)


def _skus_almost_complete_from_wide(df_wide: pl.DataFrame, threshold: float = 0.8) -> tuple[int, float, int]:
    """SKUs with at least threshold (e.g. 80%) of attributes in MATCH. Returns (count, pct, total)."""
    check_cols = [c for c in df_wide.columns if c.endswith(' Check')]
    if not check_cols or len(df_wide) == 0:
        return 0, 0.0, len(df_wide)
    n_attrs = len(check_cols)
    match_count_expr = pl.sum_horizontal([(pl.col(c) == 'MATCH').cast(pl.Int32) for c in check_cols])
    pct_match_per_sku = match_count_expr / n_attrs
    almost = df_wide.filter(pct_match_per_sku >= threshold).height
    total = len(df_wide)
    pct = (almost / total * 100) if total else 0.0
    return almost, round(pct, 1), total


def _mean_match_pct_per_sku_from_wide(df_wide: pl.DataFrame) -> float:
    """Average over SKUs of (nb MATCH / nb attributes)*100."""
    check_cols = [c for c in df_wide.columns if c.endswith(' Check')]
    if not check_cols or len(df_wide) == 0:
        return 0.0
    n_attrs = len(check_cols)
    match_count_expr = pl.sum_horizontal([(pl.col(c) == 'MATCH').cast(pl.Int32) for c in check_cols])
    pct_expr = match_count_expr / n_attrs * 100
    mean_val = df_wide.select(pct_expr.mean()).item()
    return round(float(mean_val), 1)


def _pct_skus_zero_matching_from_wide(df_wide: pl.DataFrame) -> tuple[float, int, int]:
    """% of SKUs with 0 matching attributes (no attribute is MATCH). Returns (pct, count, total)."""
    check_cols = [c for c in df_wide.columns if c.endswith(' Check')]
    if not check_cols or len(df_wide) == 0:
        return 0.0, 0, len(df_wide)
    match_count_expr = pl.sum_horizontal([(pl.col(c) == 'MATCH').cast(pl.Int32) for c in check_cols])
    zero_matching = df_wide.filter(match_count_expr == 0).height
    total = len(df_wide)
    pct = (zero_matching / total * 100) if total else 0.0
    return round(pct, 1), zero_matching, total


def _get_skus_complete_df(df_wide: pl.DataFrame) -> pl.DataFrame:
    """Full comparison table (all columns) for SKUs that are complete (all attributes MATCH)."""
    check_cols = [c for c in df_wide.columns if c.endswith(' Check')]
    if not check_cols or len(df_wide) == 0:
        return pl.DataFrame()
    all_match = pl.lit(True)
    for c in check_cols:
        all_match = all_match & (pl.col(c) == 'MATCH')
    return df_wide.filter(all_match)


def _get_skus_almost_complete_df(df_wide: pl.DataFrame, threshold: float = 0.8) -> pl.DataFrame:
    """Full comparison table (all columns) for SKUs with >= threshold attributes MATCH."""
    check_cols = [c for c in df_wide.columns if c.endswith(' Check')]
    if not check_cols or len(df_wide) == 0:
        return pl.DataFrame()
    n_attrs = len(check_cols)
    match_count_expr = pl.sum_horizontal([(pl.col(c) == 'MATCH').cast(pl.Int32) for c in check_cols])
    pct_expr = match_count_expr / n_attrs
    return df_wide.filter(pct_expr >= threshold)


def _get_skus_zero_matching_df(df_wide: pl.DataFrame) -> pl.DataFrame:
    """Full comparison table (all columns) for SKUs with 0 matching attributes."""
    check_cols = [c for c in df_wide.columns if c.endswith(' Check')]
    if not check_cols or len(df_wide) == 0:
        return pl.DataFrame()
    match_count_expr = pl.sum_horizontal([(pl.col(c) == 'MATCH').cast(pl.Int32) for c in check_cols])
    return df_wide.filter(match_count_expr == 0)


def _get_skus_with_mismatch_df(df_wide: pl.DataFrame) -> pl.DataFrame:
    """Full comparison rows for SKUs that have at least one MISMATCH (download mismatches only)."""
    check_cols = [c for c in df_wide.columns if c.endswith(' Check')]
    if not check_cols or len(df_wide) == 0:
        return pl.DataFrame()
    has_mismatch = pl.any_horizontal([pl.col(c) == 'MISMATCH' for c in check_cols])
    return df_wide.filter(has_mismatch)


def _top3_mismatch_attributes_from_wide(df_wide: pl.DataFrame, top_n: int = 3) -> pl.DataFrame:
    """Top N attributes by highest mismatch %."""
    by_attr = _match_and_valid_pct_by_attribute_from_wide(df_wide)
    if len(by_attr) == 0:
        return pl.DataFrame()
    by_attr = by_attr.with_columns((100 - pl.col('match_pct')).alias('mismatch_pct'))
    return by_attr.sort('mismatch_pct', descending=True).head(top_n).select([
        pl.col('attribute'),
        pl.col('mismatch_pct'),
        pl.col('match_pct'),
    ])


def render_kpi_section(stats: dict, comparison_wide: pl.DataFrame | None = None):
    """Render KPI section: % SKUs complete, almost complete, mean match %, % zero valid, top 3 mismatch."""
    st.header("Key metrics")
    
    if comparison_wide is not None and len(comparison_wide) > 0:
        total_skus = len(comparison_wide)
        pct_complete, complete_count, _ = _pct_skus_complete_from_wide(comparison_wide)
        almost_count, almost_pct, _ = _skus_almost_complete_from_wide(comparison_wide, threshold=0.8)
        mean_match = _mean_match_pct_per_sku_from_wide(comparison_wide)
        pct_zero_matching, zero_matching_count, _ = _pct_skus_zero_matching_from_wide(comparison_wide)
        top3_mismatch = _top3_mismatch_attributes_from_wide(comparison_wide, top_n=3)
        
        col1, col2, col3, col4, col5 = st.columns(5)
        with col1:
            st.metric("Total SKUs", f"{total_skus:,}")
        with col2:
            st.metric("% SKUs complete", f"{pct_complete}%", delta=f"{complete_count:,}")
            df_complete = _get_skus_complete_df(comparison_wide)
            if len(df_complete) > 0:
                st.download_button("Download .xlsx", data=export_to_excel_bytes(df_complete), file_name="skus_complete.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="dl_complete")
            else:
                st.caption("No SKUs complete")
        with col3:
            st.metric('SKUs "almost complete" (≥80% match)', f"{almost_count:,}", delta=f"{almost_pct}%")
            df_almost = _get_skus_almost_complete_df(comparison_wide)
            if len(df_almost) > 0:
                st.download_button("Download .xlsx", data=export_to_excel_bytes(df_almost), file_name="skus_almost_complete.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="dl_almost")
            else:
                st.caption("None")
        with col4:
            st.metric("Mean match % per SKU", f"{mean_match}%", delta="average")
        with col5:
            st.metric("% SKUs with 0 matching attributes", f"{pct_zero_matching}%", delta=f"{zero_matching_count:,}")
            df_zero = _get_skus_zero_matching_df(comparison_wide)
            if len(df_zero) > 0:
                st.download_button("Download .xlsx", data=export_to_excel_bytes(df_zero), file_name="skus_zero_matching.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="dl_zero_match")
            else:
                st.caption("None")
        
        st.subheader("Top 3 attributes (highest % mismatch)")
        if len(top3_mismatch) > 0:
            st.dataframe(
                top3_mismatch.to_pandas(),
                use_container_width=True,
                hide_index=True,
                column_config={
                    'attribute': st.column_config.TextColumn('Attribute'),
                    'mismatch_pct': st.column_config.NumberColumn('% mismatch', format='%.1f'),
                    'match_pct': st.column_config.NumberColumn('% match', format='%.1f'),
                }
            )
        else:
            st.info("No attribute check columns found.")
    else:
        total_skus = stats.get('total_skus', 0)
        st.metric("Total SKUs", f"{total_skus:,}")
        st.info("Load precomputed data (output/) to see all metrics.")
    
    st.subheader("% match and % valid by attribute")
    st.caption("Match = STIBO and SAP agree. Valid = non-empty (both sides have a value to compare).")
    if comparison_wide is not None and len(comparison_wide) > 0:
        by_attr = _match_and_valid_pct_by_attribute_from_wide(comparison_wide)
        if len(by_attr) > 0:
            st.dataframe(
                by_attr.to_pandas(),
                use_container_width=True,
                hide_index=True,
                column_config={
                    'attribute': st.column_config.TextColumn('Attribute'),
                    'match_pct': st.column_config.NumberColumn('% match', format='%.1f'),
                    'valid_pct': st.column_config.NumberColumn('% valid (non-empty)', format='%.1f'),
                }
            )
        else:
            st.info("No attribute check columns found.")
    else:
        st.info("Load precomputed data (output/) to see % match and % valid by attribute.")


def render_filters(comparisons_df: pl.DataFrame):
    """Render filters and return filtered DataFrame."""
    st.header("Filters")
    
    # Search bar for item (SKU / code)
    search_term = st.text_input(
        "Search item",
        placeholder="Type SKU or item code to search...",
        key="item_search"
    )
    
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        available_statuses = sorted(comparisons_df['status'].unique().to_list())
        selected_statuses = st.multiselect(
            "Status",
            options=available_statuses,
            default=available_statuses
        )
    
    with col2:
        if 'vendor' in comparisons_df.columns:
            vendors = sorted(comparisons_df['vendor'].drop_nulls().unique().to_list())
            selected_vendors = st.multiselect(
                "Vendor",
                options=vendors,
                default=vendors
            )
        else:
            selected_vendors = None
    
    with col3:
        if 'brand' in comparisons_df.columns:
            brands = sorted(comparisons_df['brand'].drop_nulls().unique().to_list())
            selected_brands = st.multiselect(
                "Brand",
                options=brands,
                default=brands
            )
        else:
            selected_brands = None
    
    with col4:
        if 'in_range' in comparisons_df.columns:
            in_range_values = sorted(comparisons_df['in_range'].drop_nulls().unique().to_list())
            selected_in_range = st.multiselect(
                "In Range",
                options=in_range_values,
                default=in_range_values
            )
        else:
            selected_in_range = None
    
    filtered_df = comparisons_df
    
    if search_term and search_term.strip():
        q = search_term.strip().lower()
        filtered_df = filtered_df.filter(
            pl.col('sku').cast(pl.Utf8).str.to_lowercase().str.contains(q)
        )
    
    if selected_statuses:
        filtered_df = filtered_df.filter(pl.col('status').is_in(selected_statuses))
    
    if selected_vendors is not None and 'vendor' in comparisons_df.columns:
        filtered_df = filtered_df.filter(pl.col('vendor').is_in(selected_vendors))
    
    if selected_brands is not None and 'brand' in comparisons_df.columns:
        filtered_df = filtered_df.filter(pl.col('brand').is_in(selected_brands))
    
    if selected_in_range is not None and 'in_range' in comparisons_df.columns:
        filtered_df = filtered_df.filter(pl.col('in_range').is_in(selected_in_range))
    
    return filtered_df


def style_comparison_table(df: pd.DataFrame) -> pd.DataFrame:
    """Applique le formatage conditionnel au tableau."""
    def color_status(val):
        if val == 'MATCH':
            return 'background-color: #90EE90'  # Vert clair
        elif val == 'MISMATCH':
            return 'background-color: #FFB6C1'  # Rouge clair
        elif val in ['MISSING_STIBO', 'MISSING_SAP', 'BOTH_MISSING']:
            return 'background-color: #FFA500'  # Orange
        return ''
    
    # Utiliser applymap pour pandas < 2.0, sinon utiliser map
    try:
        styled_df = df.style.map(
            color_status,
            subset=['status']
        )
    except AttributeError:
        # Fallback pour versions plus anciennes
        styled_df = df.style.applymap(
            color_status,
            subset=['status']
        )
    
    return styled_df


def pivot_to_wide_format(df: pl.DataFrame) -> pl.DataFrame:
    """
    Transforme le DataFrame long format en format wide (une ligne par SKU).
    Chaque attribut devient une colonne avec les valeurs normalisées STIBO et SAP.
    Optimisé pour les performances avec Polars.
    """
    # Obtenir la liste des attributs
    attributes = df['attribute'].unique().sort().to_list()
    
    # Créer la base avec tous les SKUs uniques
    all_skus = df['sku'].drop_nulls().unique().sort()
    wide_df = pl.DataFrame({'sku': all_skus})
    
    # Pour chaque attribut, créer les colonnes normalisées et joindre
    # Utiliser une seule passe pour optimiser
    for attr in attributes:
        attr_data = df.filter(pl.col('attribute') == attr).select([
            'sku',
            pl.col('stibo_value_norm').alias(f'{attr}_stibo'),
            pl.col('sap_value_norm').alias(f'{attr}_sap'),
            pl.col('status').alias(f'{attr}_status')
        ])
        
        # Joindre avec coalesce pour éviter les doublons
        wide_df = wide_df.join(attr_data, on='sku', how='left', coalesce=True)
    
    return wide_df


def style_wide_table(df: pd.DataFrame) -> pd.DataFrame:
    """Apply conditional formatting to wide comparison table (MATCH / MISMATCH colors)."""
    def color_check(val):
        if val == 'MATCH':
            return 'background-color: #B8E0D2'  # Vert doux
        elif val == 'MISMATCH':
            return 'background-color: #E8C4C4'  # Rouge doux
        elif val in ['MISSING STIBO', 'MISSING SAP', 'BOTH MISSING']:
            return 'background-color: #F5D7A0'  # Ambre / orange doux
        return ''
    
    # Find all "Check" columns
    check_cols = [col for col in df.columns if col.endswith(' Check')]
    
    styled_df = df.style
    for col in check_cols:
        styled_df = styled_df.map(color_check, subset=[col])
    
    return styled_df


SAMPLE_ROWS = 20


def render_main_table_wide(df_wide: pl.DataFrame):
    """Render a sample of the comparison table + search and filters."""
    st.header("Comparison Table")
    
    sku_col = 'SKU / Item Code'
    check_cols = [c for c in df_wide.columns if c.endswith(' Check')]
    
    # Legend
    with st.container():
        st.markdown("**Legend**")
        leg1, leg2, leg3, leg4 = st.columns(4)
        with leg1:
            st.markdown(":green[**MATCH**] — values agree")
        with leg2:
            st.markdown(":red[**MISMATCH**] — values differ")
        with leg3:
            st.markdown(":orange[**MISSING STIBO/SAP**] — missing on one side")
        with leg4:
            st.markdown(":gray[**BOTH MISSING**]")
        st.divider()
    
    # Filters row: search + "mismatches only" toggle
    col_search, col_filter, col_dl = st.columns([2, 1, 1])
    with col_search:
        search_term = st.text_input(
            "Search SKU / Item Code",
            placeholder="Type part of SKU to filter...",
            key="item_search_wide"
        )
    with col_filter:
        show_mismatches_only = st.checkbox(
            "Only rows with ≥1 MISMATCH",
            value=False,
            key="table_show_mismatches_only"
        )
    with col_dl:
        try:
            full_excel = export_to_excel_bytes(df_wide)
            st.download_button(
                label="Download full table (.xlsx)",
                data=full_excel,
                file_name="comparison_table.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="download_comparison_table_section",
            )
        except Exception as e:
            st.caption(f"Export: {e}")
    
    # Apply filters
    base_df = df_wide
    if show_mismatches_only and check_cols:
        base_df = _get_skus_with_mismatch_df(df_wide)
        if len(base_df) == 0:
            st.info("No rows with at least one MISMATCH.")
            return
    
    if search_term and search_term.strip():
        q = search_term.strip()
        filtered_df = base_df.filter(
            pl.col(sku_col).cast(pl.Utf8).str.to_lowercase().str.contains(q.lower())
        )
        display_df = filtered_df.head(SAMPLE_ROWS)
        caption = f"{len(filtered_df)} row(s) match search — showing up to {SAMPLE_ROWS}."
        if len(filtered_df) == 0:
            st.info("No row matches your search.")
            return
    else:
        display_df = base_df.head(SAMPLE_ROWS)
        total_base = len(base_df)
        caption = f"Showing up to {SAMPLE_ROWS} rows (total: {total_base:,} SKUs). Use search or « Only rows with ≥1 MISMATCH » to narrow."
    
    df_pandas = display_df.to_pandas()
    n_cells = df_pandas.size
    max_styler = pd.get_option("styler.render.max_elements")
    
    if max_styler is not None and n_cells > max_styler:
        st.dataframe(df_pandas, use_container_width=True, hide_index=True, height=480)
    else:
        styled_df = style_wide_table(df_pandas)
        st.dataframe(styled_df, use_container_width=True, hide_index=True, height=480)
    
    st.caption(caption)
    
    # Optional: download current view (filtered sample) as Excel
    if show_mismatches_only and len(base_df) > 0 and len(base_df) <= 10_000:
        st.caption("Download the « mismatches only » list from the Key metrics section above, or use the full table download for the complete dataset.")


def render_main_table(filtered_df: pl.DataFrame):
    """Render main comparison table in wide format (one row per SKU)."""
    st.header("Comparison table by SKU")
    
    if len(filtered_df) == 0:
        st.warning("No results match the selected filters.")
        return
    
    with st.spinner("Building wide table..."):
        wide_df = pivot_to_wide_format(filtered_df)
    
    MAX_DISPLAY_ROWS = 1000
    total_skus = len(wide_df)
    
    if total_skus > MAX_DISPLAY_ROWS:
        st.warning(f"Too many SKUs ({total_skus:,}). Showing first {MAX_DISPLAY_ROWS:,}. Use filters to narrow results.")
        wide_df = wide_df.head(MAX_DISPLAY_ROWS)
    
    display_df = wide_df.to_pandas()
    
    current_limit = pd.get_option("styler.render.max_elements")
    required_limit = len(display_df) * len(display_df.columns)
    if required_limit > current_limit:
        pd.set_option("styler.render.max_elements", max(required_limit, 2000000))
    
    cell_count = len(display_df) * len(display_df.columns)
    if cell_count > 500000:
        st.info("Table too large for conditional formatting. Displaying without colors.")
        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True
        )
    else:
        # Appliquer le formatage conditionnel sur les colonnes de statut
        def color_status_cells(val):
            if val == 'MATCH':
                return 'background-color: #90EE90'  # Vert clair
            elif val == 'MISMATCH':
                return 'background-color: #FFB6C1'  # Rouge clair
            elif val in ['MISSING_STIBO', 'MISSING_SAP', 'BOTH_MISSING']:
                return 'background-color: #FFA500'  # Orange
            return ''
        
        # Appliquer le formatage sur toutes les colonnes de statut
        status_cols = [col for col in display_df.columns if col.endswith('_status')]
        if status_cols:
            try:
                styled_df = display_df.style.map(
                    color_status_cells,
                    subset=status_cols
                )
            except AttributeError:
                styled_df = display_df.style.applymap(
                    color_status_cells,
                    subset=status_cols
                )
            st.dataframe(
                styled_df,
                use_container_width=True,
                hide_index=True
            )
        else:
            st.dataframe(
                display_df,
                use_container_width=True,
                hide_index=True
            )
    
    st.caption(f"Showing {len(wide_df):,} of {total_skus:,} SKUs.")


def render_drill_down(comparisons_df: pl.DataFrame):
    """Render detail view for a single SKU."""
    st.header("Detail view by SKU")
    
    available_skus = sorted(comparisons_df['sku'].drop_nulls().unique().to_list())
    selected_sku = st.selectbox(
        "Select a SKU",
        options=available_skus
    )
    
    if selected_sku:
        sku_data = comparisons_df.filter(pl.col('sku') == selected_sku)
        
        if len(sku_data) > 0:
            st.subheader(f"Comparisons for SKU: {selected_sku}")
            
            display_df = sku_data.select([
                'attribute',
                'stibo_value_raw',
                'sap_value_raw',
                'stibo_value_norm',
                'sap_value_norm',
                'status',
                'diff_type'
            ]).to_pandas()
            
            styled_df = style_comparison_table(display_df)
            st.dataframe(
                styled_df,
                use_container_width=True,
                hide_index=True
            )
        else:
            st.warning(f"No data found for SKU: {selected_sku}")


def export_to_excel_bytes(df: pl.DataFrame) -> bytes:
    """Export DataFrame to Excel in memory and return bytes."""
    try:
        from io import BytesIO
        df_pandas = df.to_pandas()
        buffer = BytesIO()
        df_pandas.to_excel(buffer, index=False, engine='openpyxl')
        buffer.seek(0)
        return buffer.getvalue()
    except Exception as e:
        logger.error(f"Export error: {e}")
        raise


def render_inner_outer_section():
    """Inner = Outer (non-Generic): matched Inner/Outer rows, Excel export with alternating row highlight."""
    df_stibo = st.session_state.get("df_stibo")
    if df_stibo is None or len(df_stibo) == 0:
        return
    st.header("Inner = Outer (non-Generic)")
    st.caption("For each Inner GTIN (non Generic/Placeholder) that matches an Outer GTIN: source rows matched as pairs. Excel export: alternating green fill for readability.")
    same_le, diff_le = build_inner_outer_non_generic(df_stibo)
    export_df = build_inner_outer_export_rows(df_stibo)
    st.session_state.inner_outer_export_df = export_df
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Same Legal Entity")
        if len(same_le) > 0:
            st.dataframe(same_le.to_pandas(), use_container_width=True, hide_index=True)
            st.caption(f"{len(same_le)} match(es)")
        else:
            st.info("None.")
    with col2:
        st.subheader("Different Legal Entities")
        if len(diff_le) > 0:
            st.dataframe(diff_le.to_pandas(), use_container_width=True, hide_index=True)
            st.caption(f"{len(diff_le)} match(es)")
        else:
            st.info("None.")
    st.subheader("Export: matched Inner / Outer rows")
    if len(export_df) > 0:
        st.dataframe(export_df.head(200).to_pandas(), use_container_width=True, hide_index=True)
        st.caption(f"Preview (max 200 rows). Total: {len(export_df)} rows (Inner then Outer pairs).")
        try:
            excel_bytes = export_inner_outer_to_excel_bytes(export_df)
            st.download_button(
                label="Download as Excel",
                data=excel_bytes,
                file_name="inner_outer_matched.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="inner_outer_download",
            )
        except Exception as e:
            st.error(f"Excel export error: {e}")
            logger.exception("Export inner/outer")
    else:
        st.info("No Inner=Outer (non-Generic) pairs to export.")


def render_export_section_wide(df_wide: pl.DataFrame):
    """Export section for wide format."""
    st.header("Export")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        excel_bytes = export_to_excel_bytes(df_wide)
        st.download_button(
            label="Download comparison table (.xlsx)",
            data=excel_bytes,
            file_name="comparison_table.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="download_comparison_export_section",
        )
    
    with col2:
        # Export STIBO only
        if st.session_state.stibo_only is not None and len(st.session_state.stibo_only) > 0:
            stibo_only_bytes = export_to_excel_bytes(st.session_state.stibo_only)
            st.download_button(
                label=f"Download STIBO-only ({len(st.session_state.stibo_only)} rows)",
                data=stibo_only_bytes,
                file_name="stibo_only.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        else:
            st.info("No STIBO-only products")
    
    with col3:
        # Export SAP only
        if st.session_state.sap_only is not None and len(st.session_state.sap_only) > 0:
            sap_only_bytes = export_to_excel_bytes(st.session_state.sap_only)
            st.download_button(
                label=f"Download SAP-only ({len(st.session_state.sap_only)} rows)",
                data=sap_only_bytes,
                file_name="sap_only.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        else:
            st.info("No SAP-only products")


def render_export_section(filtered_df: pl.DataFrame):
    """Render export section."""
    st.header("Export")
    
    col1, col2 = st.columns(2)
    
    with col1:
        mismatches_df = st.session_state.comparisons_df.filter(
            pl.col('status') == 'MISMATCH'
        )
        
        if len(mismatches_df) > 0:
            try:
                excel_bytes = export_to_excel_bytes(mismatches_df)
                st.download_button(
                    label="Export all mismatches",
                    data=excel_bytes,
                    file_name="mismatches_export.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            except Exception as e:
                st.error(f"Export error: {e}")
        else:
            st.info("No mismatch to export.")
    
    with col2:
        if len(filtered_df) > 0:
            try:
                excel_bytes = export_to_excel_bytes(filtered_df)
                st.download_button(
                    label="Export filtered view",
                    data=excel_bytes,
                    file_name="filtered_export.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            except Exception as e:
                st.error(f"Export error: {e}")
        else:
            st.info("No data to export with current filters.")


def main():
    """Main application entry point."""
    st.title("STIBO vs SAP Reconciliation Dashboard")
    
    with st.sidebar:
        st.header("Settings")
        
        mapping = load_column_mapping()
        available_markets = list(mapping.get('join_key', {}).keys())
        
        if available_markets:
            selected_market = st.selectbox(
                "Market",
                options=available_markets,
                index=0 if 'Brakes' in available_markets else 0,
                key='market_selector'
            )
            st.session_state.selected_market = selected_market
        else:
            st.warning("No market configured in column_mapping.json")
            st.session_state.selected_market = 'Brakes'
        
        st.divider()
        
        # Option 1: Load from precomputed
        st.subheader("Load precomputed")
        if st.button("Load from output/", type="primary"):
            if load_from_precomputed():
                st.success("Precomputed data loaded successfully.")
                st.rerun()
            else:
                st.error("Failed to load precomputed data.")
        
        st.divider()
        
        # Option 2: Recompute from Excel
        st.subheader("Recompute from Excel")
        excel_file = st.text_input(
            "Excel file path",
            value="STIBO Brakes Product Full Extract - 09.01.26 - sent Ilyass.xlsx",
            key="excel_file_input"
        )
        
        if st.button("Recompute", type="secondary"):
            if excel_file and Path(excel_file).exists():
                if run_build_comparison(excel_file, st.session_state.selected_market):
                    # Auto-load after recompute
                    if load_from_precomputed():
                        st.success("Recomputed and loaded successfully!")
                        st.rerun()
            else:
                st.error(f"Excel file not found: {excel_file}")
        
        st.divider()
        
        # Option 3: Load directly from Excel (legacy)
        st.subheader("Load from Excel (legacy)")
        if st.button("Load data", type="secondary"):
            if load_data():
                st.success("Data loaded successfully.")
            else:
                st.error("Failed to load data.")
        
        if st.session_state.comparison_wide is not None or st.session_state.comparisons_df is not None:
            st.success("Data loaded")
            stats = st.session_state.stats or {}
            if isinstance(stats, dict):
                st.info(f"SKUs: {stats.get('total_skus', 'N/A')}")
                st.info(f"Comparisons: {stats.get('total_comparisons', 'N/A')}")
    
    if st.session_state.comparison_wide is None and st.session_state.comparisons_df is None:
        st.info("Load data from the sidebar to get started.")
        return
    
    # Display sections
    render_kpi_section(st.session_state.stats, st.session_state.get('comparison_wide'))
    
    st.divider()
    
    # Use wide format if available, otherwise use long format
    filtered_df = None
    if st.session_state.comparison_wide is not None:
        render_main_table_wide(st.session_state.comparison_wide)
    else:
        filtered_df = render_filters(st.session_state.comparisons_df)
        st.divider()
        render_main_table(filtered_df)
        st.divider()
        render_drill_down(st.session_state.comparisons_df)
    
    st.divider()
    
    render_inner_outer_section()
    
    st.divider()
    
    # Export section
    if st.session_state.comparison_wide is not None:
        render_export_section_wide(st.session_state.comparison_wide)
    elif filtered_df is not None:
        render_export_section(filtered_df)


if __name__ == "__main__":
    main()
