"""
Optimized comparison engine using vectorized Polars operations.
"""

import polars as pl
from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


def _normalize_barcode_polars(col: pl.Expr) -> pl.Expr:
    """Vectorized barcode normalization: remove non-numeric characters."""
    return (
        col.cast(pl.Utf8)
        .str.replace_all(r"[^0-9]", "")
        .map_elements(lambda x: x if x and len(x) > 0 else None, return_dtype=pl.Utf8)
    )


def _normalize_text_polars(col: pl.Expr) -> pl.Expr:
    """Vectorized text normalization: trim and lowercase."""
    return col.cast(pl.Utf8).str.strip_chars().str.to_lowercase()


def _normalize_exact_polars(col: pl.Expr) -> pl.Expr:
    """Vectorized exact normalization: trim only."""
    return col.cast(pl.Utf8).str.strip_chars()


def _normalize_packsize_polars(col: pl.Expr) -> pl.Expr:
    """Vectorized packsize normalization: extract #NBRx#NBR pattern using map_elements for regex."""
    # Use map_elements for complex regex extraction (Polars doesn't support full regex capture groups easily)
    from config import normalize_packsize
    return col.map_elements(normalize_packsize, return_dtype=pl.Utf8)


def _get_normalizer_polars(normalizer_name: str) -> callable:
    """Get Polars vectorized normalizer function."""
    normalizers = {
        'normalize_barcode': _normalize_barcode_polars,
        'normalize_text': _normalize_text_polars,
        'normalize_exact': _normalize_exact_polars,
        'normalize_packsize': _normalize_packsize_polars,
    }
    return normalizers.get(normalizer_name, _normalize_exact_polars)


def build_comparison_table(
    df_stibo: pl.DataFrame,
    df_sap: pl.DataFrame,
    attribute_config: List[Dict[str, Any]],
    context_columns: Dict[str, Dict[str, str]],
    join_key: str,
    df_elist: Optional[pl.DataFrame] = None,
    df_current_range: Optional[pl.DataFrame] = None
) -> pl.DataFrame:
    """
    Build comparison table using fully vectorized Polars operations.
    Much faster than Python loops.
    """
    # Brand from Elist is applied in load_and_prepare_data (before SAP key rename) for reliable join.
    
    # Enrich SAP with Vendor from Current Range
    if df_current_range is not None:
        if 'Product' in df_current_range.columns and 'Ult Ven Name' in df_current_range.columns:
            df_current_range_vendor = df_current_range.select(['Product', 'Ult Ven Name']).rename({'Product': join_key})
            df_current_range_vendor = df_current_range_vendor.unique(subset=[join_key], keep='first')
            if 'Ult Ven Name' in df_sap.columns:
                df_sap = df_sap.drop('Ult Ven Name')
            df_sap = df_sap.join(df_current_range_vendor, on=join_key, how='left')
            logger.info("SAP Vendor enriched from Current Range")
    
    # Inner join: only SKUs present in both
    df_merged = df_stibo.join(df_sap, on=join_key, how='inner', suffix='_sap')
    
    if len(df_merged) == 0:
        logger.warning("No matching SKUs found")
        return pl.DataFrame()
    
    # Build comparison rows using vectorized operations
    comparison_dfs = []
    
    for attr_config in attribute_config:
        attribute = attr_config['attribute']
        stibo_col = attr_config['stibo_column']
        sap_col = attr_config['sap_column']
        stibo_norm_name = attr_config.get('stibo_normalizer', None)
        sap_norm_name = attr_config.get('sap_normalizer', None)
        comparison_mode = attr_config.get('comparison_mode', 'exact')
        
        # Check if columns exist
        if stibo_col not in df_stibo.columns:
            logger.warning(f"STIBO column '{stibo_col}' not found for attribute '{attribute}'")
            continue
        if sap_col not in df_sap.columns:
            logger.warning(f"SAP column '{sap_col}' not found for attribute '{attribute}'")
            continue
        
        # Select columns for this attribute
        # SAP columns may have _sap suffix after join
        sap_col_actual = sap_col if sap_col in df_merged.columns else f"{sap_col}_sap" if f"{sap_col}_sap" in df_merged.columns else None
        
        cols_to_select = [join_key]
        if stibo_col in df_merged.columns:
            cols_to_select.append(stibo_col)
        if sap_col_actual and sap_col_actual in df_merged.columns:
            cols_to_select.append(sap_col_actual)
        
        # Add context columns (SAP columns may have _sap suffix)
        for ctx_key, ctx_col in context_columns.get('stibo', {}).items():
            if ctx_col in df_merged.columns:
                cols_to_select.append(ctx_col)
        for ctx_key, ctx_col in context_columns.get('sap', {}).items():
            ctx_col_actual = ctx_col if ctx_col in df_merged.columns else f"{ctx_col}_sap" if f"{ctx_col}_sap" in df_merged.columns else None
            if ctx_col_actual and ctx_col_actual in df_merged.columns:
                cols_to_select.append(ctx_col_actual)
        
        df_attr = df_merged.select(cols_to_select)
        
        # Get raw values (use actual column name for SAP)
        stibo_raw = pl.col(stibo_col) if stibo_col in df_attr.columns else pl.lit(None)
        sap_raw_col = sap_col_actual if sap_col_actual else sap_col
        sap_raw = pl.col(sap_raw_col) if sap_raw_col in df_attr.columns else pl.lit(None)
        
        # Normalize using Polars expressions or map_elements
        if stibo_norm_name:
            norm_name = stibo_norm_name.__name__ if hasattr(stibo_norm_name, '__name__') else str(stibo_norm_name)
            if norm_name == 'normalize_packsize':
                # Packsize needs complex regex, use map_elements
                stibo_norm = stibo_raw.map_elements(stibo_norm_name, return_dtype=pl.Utf8)
            else:
                stibo_norm_func = _get_normalizer_polars(norm_name)
                stibo_norm = stibo_norm_func(stibo_raw)
        else:
            stibo_norm = stibo_raw.cast(pl.Utf8)
        
        if sap_norm_name:
            norm_name = sap_norm_name.__name__ if hasattr(sap_norm_name, '__name__') else str(sap_norm_name)
            if norm_name == 'normalize_packsize':
                # Packsize needs complex regex, use map_elements
                sap_norm = sap_raw.map_elements(sap_norm_name, return_dtype=pl.Utf8)
            else:
                sap_norm_func = _get_normalizer_polars(norm_name)
                sap_norm = sap_norm_func(sap_raw)
        else:
            sap_norm = sap_raw.cast(pl.Utf8)
        
        # Compare vectorized
        if comparison_mode == 'case_insensitive':
            are_equal = stibo_norm.str.to_lowercase() == sap_norm.str.to_lowercase()
        else:
            are_equal = stibo_norm == sap_norm
        
        # Determine status vectorized
        stibo_is_null = stibo_raw.is_null() | (stibo_raw.cast(pl.Utf8) == "nan")
        sap_is_null = sap_raw.is_null() | (sap_raw.cast(pl.Utf8) == "nan")
        
        status = (
            pl.when(stibo_is_null & sap_is_null).then(pl.lit("BOTH_MISSING"))
            .when(stibo_is_null).then(pl.lit("MISSING_STIBO"))
            .when(sap_is_null).then(pl.lit("MISSING_SAP"))
            .when(are_equal).then(pl.lit("MATCH"))
            .otherwise(pl.lit("MISMATCH"))
        )
        
        # Determine diff_type
        stibo_raw_str = stibo_raw.cast(pl.Utf8).str.strip_chars().str.to_lowercase()
        sap_raw_str = sap_raw.cast(pl.Utf8).str.strip_chars().str.to_lowercase()
        raw_equal = stibo_raw_str == sap_raw_str
        
        diff_type = (
            pl.when(status.is_in(["MISSING_STIBO", "MISSING_SAP", "BOTH_MISSING"])).then(pl.lit(None))
            .when(status == "MATCH")
            .then(pl.when(raw_equal).then(pl.lit("EXACT")).otherwise(pl.lit("FORMAT_ONLY")))
            .otherwise(pl.lit("REAL_DIFF"))
        )
        
        # Build comparison DataFrame for this attribute (cast raw to Utf8 so concat schema is consistent)
        select_cols = [
            pl.col(join_key).alias('sku'),
            pl.lit(attribute).alias('attribute'),
            stibo_raw.cast(pl.Utf8).alias('stibo_value_raw'),
            sap_raw.cast(pl.Utf8).alias('sap_value_raw'),
            stibo_norm.alias('stibo_value_norm'),
            sap_norm.alias('sap_value_norm'),
            status.alias('status'),
            diff_type.alias('diff_type'),
        ]
        
        # Add context columns to select (cast to Utf8 for consistent concat schema)
        for ctx_key, ctx_col in context_columns.get('stibo', {}).items():
            if ctx_col in df_attr.columns:
                select_cols.append(pl.col(ctx_col).cast(pl.Utf8).alias(ctx_key))
        for ctx_key, ctx_col in context_columns.get('sap', {}).items():
            ctx_col_actual = ctx_col if ctx_col in df_attr.columns else f"{ctx_col}_sap" if f"{ctx_col}_sap" in df_attr.columns else None
            if ctx_col_actual and ctx_col_actual in df_attr.columns:
                select_cols.append(pl.col(ctx_col_actual).cast(pl.Utf8).alias(ctx_key))
        
        df_comp = df_attr.select(select_cols)
        
        comparison_dfs.append(df_comp)
    
    # Concatenate all attribute comparisons
    if comparison_dfs:
        df_comparisons = pl.concat(comparison_dfs)
        logger.info(f"Comparison table created: {len(df_comparisons)} rows")
        return df_comparisons
    else:
        logger.warning("No comparison rows created")
        return pl.DataFrame()


def get_comparison_statistics(df_comparisons: pl.DataFrame) -> Dict[str, Any]:
    """Calculate comparison statistics."""
    if len(df_comparisons) == 0:
        return {
            'total_skus': 0,
            'total_comparisons': 0,
            'match_count': 0,
            'mismatch_count': 0,
            'missing_stibo_count': 0,
            'missing_sap_count': 0,
            'both_missing_count': 0,
            'match_pct': 0.0,
            'mismatch_pct': 0.0,
            'mismatch_by_attribute': pl.DataFrame(),
        }
    
    total_comparisons = len(df_comparisons)
    total_skus = df_comparisons['sku'].n_unique()
    
    # Count statuses vectorized
    status_counts = df_comparisons['status'].value_counts().sort('count', descending=True)
    status_dict = dict(zip(status_counts['status'].to_list(), status_counts['count'].to_list()))
    
    stats = {
        'total_skus': total_skus,
        'total_comparisons': total_comparisons,
        'match_count': status_dict.get('MATCH', 0),
        'mismatch_count': status_dict.get('MISMATCH', 0),
        'missing_stibo_count': status_dict.get('MISSING_STIBO', 0),
        'missing_sap_count': status_dict.get('MISSING_SAP', 0),
        'both_missing_count': status_dict.get('BOTH_MISSING', 0),
    }
    
    if total_comparisons > 0:
        stats['match_pct'] = (stats['match_count'] / total_comparisons) * 100
        stats['mismatch_pct'] = (stats['mismatch_count'] / total_comparisons) * 100
    else:
        stats['match_pct'] = 0.0
        stats['mismatch_pct'] = 0.0
    
    # Mismatch by attribute (vectorized)
    mismatch_by_attr = (
        df_comparisons
        .filter(pl.col('status') == 'MISMATCH')
        .group_by('attribute')
        .agg([
            pl.count().alias('mismatch_count'),
            pl.col('sku').n_unique().alias('sku_count')
        ])
        .sort('mismatch_count', descending=True)
    )
    
    stats['mismatch_by_attribute'] = mismatch_by_attr
    
    return stats
