"""
Script CLI pour pré-calculer le tableau de comparaison STIBO vs SAP.
Génère les fichiers dans output/ : comparison.parquet, stats.json, stibo_only.parquet, sap_only.parquet
"""

import argparse
import json
import logging
from pathlib import Path
import polars as pl
from typing import Dict, Any

from loaders import load_and_prepare_data, get_excel_columns
from comparison_engine import build_comparison_table, get_comparison_statistics
from mapping_loader import (
    load_column_mapping,
    detect_columns,
    build_attribute_config_from_mapping
)
from inner_outer_analysis import (
    build_inner_outer_non_generic,
    build_inner_outer_export_rows,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def build_wide_comparison_table(
    df_comparison_long: pl.DataFrame,
    df_stibo: pl.DataFrame,
    df_sap: pl.DataFrame,
    attribute_config: list,
    context_columns: Dict[str, Dict[str, str]],
    join_key: str
) -> pl.DataFrame:
    """
    Transforme le tableau long (une ligne par SKU+attribut) en format wide (une ligne par SKU).
    Format: SKU, Product Name (STIBO), Product Name (SAP), Product Name Check, Packsize (STIBO), ...
    """
    if len(df_comparison_long) == 0:
        return pl.DataFrame()
    
    # Pivot pour avoir une colonne par attribut (STIBO/SAP/Check)
    wide_dfs = []
    
    for attr_config in attribute_config:
        attribute = attr_config['attribute']
        attr_display = attribute.replace('_', ' ').title()
        
        # Filtrer les lignes pour cet attribut
        df_attr = df_comparison_long.filter(pl.col('attribute') == attribute)
        
        if len(df_attr) == 0:
            continue
        
        # Créer les colonnes pour cet attribut
        df_attr_wide = df_attr.select([
            pl.col('sku'),
            pl.col('stibo_value_raw').alias(f'{attr_display} (STIBO)'),
            pl.col('sap_value_raw').alias(f'{attr_display} (SAP)'),
            pl.when(pl.col('status') == 'MATCH').then(pl.lit('MATCH'))
            .when(pl.col('status') == 'MISMATCH').then(pl.lit('MISMATCH'))
            .when(pl.col('status') == 'MISSING_STIBO').then(pl.lit('MISSING STIBO'))
            .when(pl.col('status') == 'MISSING_SAP').then(pl.lit('MISSING SAP'))
            .when(pl.col('status') == 'BOTH_MISSING').then(pl.lit('BOTH MISSING'))
            .otherwise(pl.lit('UNKNOWN'))
            .alias(f'{attr_display} Check')
        ])
        
        wide_dfs.append(df_attr_wide)
    
    # Joindre tous les attributs sur SKU (suffix pour éviter DuplicateError sur 'sku_right')
    if not wide_dfs:
        return pl.DataFrame()
    
    df_wide = wide_dfs[0]
    for df_attr in wide_dfs[1:]:
        df_wide = df_wide.join(df_attr, on='sku', how='full', suffix='_r').drop('sku_r')
    
    # Ajouter les colonnes de contexte STIBO
    for ctx_key, ctx_col in context_columns.get('stibo', {}).items():
        if ctx_col in df_stibo.columns:
            ctx_display = ctx_key.replace('_', ' ').title()
            df_ctx = df_stibo.select([
                pl.col(join_key).alias('sku'),
                pl.col(ctx_col).alias(f'{ctx_display} (STIBO)')
            ])
            df_wide = df_wide.join(df_ctx, on='sku', how='left')
    
    # Ajouter les colonnes de contexte SAP
    for ctx_key, ctx_col in context_columns.get('sap', {}).items():
        if ctx_col in df_sap.columns:
            ctx_display = ctx_key.replace('_', ' ').title()
            df_ctx = df_sap.select([
                pl.col(join_key).alias('sku'),
                pl.col(ctx_col).alias(f'{ctx_display} (SAP)')
            ])
            df_wide = df_wide.join(df_ctx, on='sku', how='left')
    
    # Renommer SKU en "SKU / Item Code"
    df_wide = df_wide.rename({'sku': 'SKU / Item Code'})
    
    # Réorganiser les colonnes : SKU en premier, puis par attribut (STIBO, SAP, Check)
    cols_ordered = ['SKU / Item Code']
    for attr_config in attribute_config:
        attribute = attr_config['attribute']
        attr_display = attribute.replace('_', ' ').title()
        cols_ordered.extend([
            f'{attr_display} (STIBO)',
            f'{attr_display} (SAP)',
            f'{attr_display} Check'
        ])
    
    # Ajouter les colonnes de contexte à la fin
    for ctx_key in context_columns.get('stibo', {}).keys():
        ctx_display = ctx_key.replace('_', ' ').title()
        cols_ordered.append(f'{ctx_display} (STIBO)')
    for ctx_key in context_columns.get('sap', {}).keys():
        ctx_display = ctx_key.replace('_', ' ').title()
        cols_ordered.append(f'{ctx_display} (SAP)')
    
    # Sélectionner seulement les colonnes qui existent
    cols_to_select = [col for col in cols_ordered if col in df_wide.columns]
    df_wide = df_wide.select(cols_to_select)
    
    return df_wide


def get_stibo_only_sap_only(
    df_stibo: pl.DataFrame,
    df_sap: pl.DataFrame,
    unified_join_key: str,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """
    Identifie les SKU présents uniquement dans STIBO ou uniquement dans SAP.
    Après load_and_prepare_data, les deux DataFrames ont la même colonne de clé (unified_join_key).
    """
    # Normaliser les clés (convertir en string)
    df_stibo_keys = df_stibo.select([
        pl.col(unified_join_key).cast(pl.Utf8).alias('sku')
    ]).unique()
    
    df_sap_keys = df_sap.select([
        pl.col(unified_join_key).cast(pl.Utf8).alias('sku')
    ]).unique()
    
    # STIBO only : dans STIBO mais pas dans SAP
    stibo_only = df_stibo_keys.join(df_sap_keys, on='sku', how='anti')
    stibo_only_df = df_stibo.join(
        stibo_only,
        left_on=unified_join_key,
        right_on='sku',
        how='inner'
    )
    if 'sku' in stibo_only_df.columns:
        stibo_only_df = stibo_only_df.drop('sku')
    
    # SAP only : dans SAP mais pas dans STIBO
    sap_only = df_sap_keys.join(df_stibo_keys, on='sku', how='anti')
    sap_only_df = df_sap.join(
        sap_only,
        left_on=unified_join_key,
        right_on='sku',
        how='inner'
    )
    if 'sku' in sap_only_df.columns:
        sap_only_df = sap_only_df.drop('sku')
    
    return stibo_only_df, sap_only_df


def main():
    parser = argparse.ArgumentParser(description='Build STIBO vs SAP comparison table')
    parser.add_argument('excel_file', type=str, help='Path to Excel file')
    parser.add_argument('--market', type=str, default='Brakes', help='Market name (default: Brakes)')
    parser.add_argument('--output-dir', type=str, default='output', help='Output directory (default: output)')
    
    args = parser.parse_args()
    
    excel_file = Path(args.excel_file)
    if not excel_file.exists():
        logger.error(f"Excel file not found: {excel_file}")
        return 1
    
    output_dir = Path(args.output_dir)
    output_dir.mkdir(exist_ok=True)
    
    logger.info(f"Loading data from {excel_file}")
    logger.info(f"Market: {args.market}")
    logger.info(f"Output directory: {output_dir}")
    
    # Noms des feuilles (à adapter selon votre fichier)
    stibo_sheet = "STIBO Seed Extract"
    sap_sheet = "SAP extract Helen"
    elist_sheet = "Elist"
    current_range_sheet = "Current Range"
    
    # Lire les en-têtes pour détecter les colonnes
    stibo_headers, stibo_name_mapping = get_excel_columns(excel_file, sheet_name=stibo_sheet, header_row=0)
    sap_headers, sap_name_mapping = get_excel_columns(excel_file, sheet_name=sap_sheet, header_row=1)
    
    # Charger le mapping et détecter les colonnes
    mapping = load_column_mapping()
    join_keys, attribute_columns, context_columns = detect_columns(
        mapping,
        stibo_headers,
        sap_headers,
        market=args.market
    )
    
    logger.info(f"Join keys: STIBO={join_keys['stibo']}, SAP={join_keys['sap']}")
    
    # Construire la liste des colonnes à charger
    stibo_cols_cleaned = [join_keys['stibo']]
    for attr_cols in attribute_columns.values():
        if attr_cols.get('stibo'):
            stibo_cols_cleaned.append(attr_cols['stibo'])
    for ctx_col in context_columns.get('stibo', {}).values():
        if ctx_col:
            stibo_cols_cleaned.append(ctx_col)
    stibo_cols_cleaned = list(set(stibo_cols_cleaned))
    
    sap_cols_cleaned = [join_keys['sap']]
    for attr_cols in attribute_columns.values():
        if attr_cols.get('sap'):
            sap_cols_cleaned.append(attr_cols['sap'])
    for ctx_col in context_columns.get('sap', {}).values():
        if ctx_col:
            sap_cols_cleaned.append(ctx_col)
    sap_cols_cleaned = list(set(sap_cols_cleaned))
    
    # Mapper les noms nettoyés vers les noms originaux
    stibo_cols_to_load = [stibo_name_mapping.get(col, col) for col in stibo_cols_cleaned if col in stibo_name_mapping]
    sap_cols_to_load = [sap_name_mapping.get(col, col) for col in sap_cols_cleaned if col in sap_name_mapping]
    
    if not stibo_cols_to_load:
        stibo_cols_to_load = None
    if not sap_cols_to_load:
        sap_cols_to_load = None
    
    # Charger les données
    from loaders import load_excel_file
    df_stibo_temp = load_excel_file(excel_file, sheet_name=stibo_sheet, header_row=0, usecols=stibo_cols_to_load)
    df_sap_temp = load_excel_file(excel_file, sheet_name=sap_sheet, header_row=1, usecols=sap_cols_to_load)
    
    # Préparer les données
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
    
    logger.info(f"STIBO: {len(df_stibo)} rows, SAP: {len(df_sap)} rows")
    
    # Construire la configuration des attributs
    attribute_config = build_attribute_config_from_mapping(attribute_columns, {})
    
    # Construire le tableau de comparaison (format long)
    logger.info("Building comparison table...")
    df_comparison_long = build_comparison_table(
        df_stibo,
        df_sap,
        attribute_config,
        context_columns,
        join_keys['stibo'],
        df_elist=df_elist,
        df_current_range=df_current_range
    )
    
    logger.info(f"Comparison table: {len(df_comparison_long)} rows")
    
    # Transformer en format wide
    logger.info("Converting to wide format...")
    df_comparison_wide = build_wide_comparison_table(
        df_comparison_long,
        df_stibo,
        df_sap,
        attribute_config,
        context_columns,
        join_keys['stibo']
    )
    
    logger.info(f"Wide comparison table: {len(df_comparison_wide)} rows, {len(df_comparison_wide.columns)} columns")
    
    # Calculer les stats
    logger.info("Calculating statistics...")
    stats = get_comparison_statistics(df_comparison_long)
    stats['market'] = args.market
    stats['total_stibo_rows'] = len(df_stibo)
    stats['total_sap_rows'] = len(df_sap)
    
    # Identifier STIBO_only et SAP_only
    logger.info("Identifying STIBO-only and SAP-only products...")
    # Après load_and_prepare_data, les deux DataFrames ont la clé unifiée (nom STIBO)
    stibo_only_df, sap_only_df = get_stibo_only_sap_only(
        df_stibo,
        df_sap,
        join_keys['stibo']
    )
    
    stats['stibo_only_count'] = len(stibo_only_df)
    stats['sap_only_count'] = len(sap_only_df)
    
    # Sauvegarder les fichiers
    logger.info(f"Saving files to {output_dir}...")
    
    # Tableau de comparaison wide
    comparison_path = output_dir / 'comparison.parquet'
    df_comparison_wide.write_parquet(comparison_path)
    logger.info(f"Saved comparison table to {comparison_path}")
    
    # Stats (convert DataFrames to list of dicts for JSON)
    stats_serializable = {}
    for k, v in stats.items():
        if isinstance(v, pl.DataFrame):
            stats_serializable[k] = v.to_dicts() if len(v) > 0 else []
        else:
            stats_serializable[k] = v
    
    stats_path = output_dir / 'stats.json'
    with open(stats_path, 'w', encoding='utf-8') as f:
        json.dump(stats_serializable, f, indent=2, ensure_ascii=False)
    logger.info(f"Saved statistics to {stats_path}")
    
    # STIBO only
    if len(stibo_only_df) > 0:
        stibo_only_path = output_dir / 'stibo_only.parquet'
        stibo_only_df.write_parquet(stibo_only_path)
        logger.info(f"Saved STIBO-only products ({len(stibo_only_df)} rows) to {stibo_only_path}")
    else:
        logger.info("No STIBO-only products found")
    
    # SAP only
    if len(sap_only_df) > 0:
        sap_only_path = output_dir / 'sap_only.parquet'
        sap_only_df.write_parquet(sap_only_path)
        logger.info(f"Saved SAP-only products ({len(sap_only_df)} rows) to {sap_only_path}")
    else:
        logger.info("No SAP-only products found")
    
    # Inner=Outer analysis
    logger.info("Building Inner=Outer analysis...")
    try:
        same_le, diff_le = build_inner_outer_non_generic(df_stibo)
        export_df = build_inner_outer_export_rows(df_stibo)
        
        if len(same_le) > 0 or len(diff_le) > 0:
            inner_outer_path = output_dir / 'inner_outer.parquet'
            export_df.write_parquet(inner_outer_path)
            logger.info(f"Saved Inner=Outer analysis ({len(export_df)} rows) to {inner_outer_path}")
        
        stats['inner_outer_same_le'] = len(same_le)
        stats['inner_outer_diff_le'] = len(diff_le)
        
        # Mettre à jour stats.json avec les nouvelles valeurs
        with open(stats_path, 'w', encoding='utf-8') as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"Could not build Inner=Outer analysis: {e}")
    
    logger.info("Done!")
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())
