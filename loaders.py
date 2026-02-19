"""
Module de chargement et préprocessing des données Excel.
"""

import polars as pl
from typing import Optional, Dict, Any, List
import logging

logger = logging.getLogger(__name__)


def get_excel_columns(file_path: str, sheet_name: Optional[str] = None, header_row: int = 0) -> tuple[List[str], Dict[str, str]]:
    """
    Read only the header row to get column names without loading all data.
    Much faster for large files.
    
    Returns:
        Tuple of (cleaned_column_names, mapping from cleaned to original)
    """
    import pandas as pd
    try:
        if sheet_name is None:
            df_header = pd.read_excel(file_path, sheet_name=0, engine='openpyxl', header=header_row, nrows=0)
        else:
            df_header = pd.read_excel(file_path, sheet_name=sheet_name, engine='openpyxl', header=header_row, nrows=0)
        cleaned_cols = [_clean_column_name(col) for col in df_header.columns]
        # Mapping: cleaned_name -> original_name
        name_mapping = {_clean_column_name(col): col for col in df_header.columns}
        return cleaned_cols, name_mapping
    except Exception as e:
        logger.warning(f"Could not read headers, will load full file: {e}")
        return [], {}


def load_excel_file(file_path: str, sheet_name: Optional[str] = None, header_row: int = 0, usecols: Optional[List[str]] = None) -> pl.DataFrame:
    """
    Charge un fichier Excel en DataFrame Polars.
    Essaie d'abord avec Polars, puis avec pandas en fallback.
    
    Args:
        file_path: Chemin vers le fichier Excel
        sheet_name: Nom de la feuille (None pour la première)
        header_row: Index de la ligne contenant les en-têtes (0 = première ligne, 1 = deuxième ligne, etc.)
        usecols: Liste optionnelle de noms de colonnes à charger (None = toutes les colonnes)
    
    Returns:
        DataFrame Polars
    """
    import pandas as pd
    
    # Essayer différents engines Polars
    # Note: fastexcel utilise calamine en interne, mais l'engine s'appelle "calamine"
    engines = ["openpyxl", "xlsx2csv"]
    
    # Essayer calamine si fastexcel est disponible
    try:
        import fastexcel
        engines.append("calamine")
    except ImportError:
        pass
    
    for engine in engines:
        try:
            # Polars ne supporte pas directement header_row, on doit utiliser pandas en fallback
            if header_row != 0:
                # Si header_row != 0, on doit utiliser pandas
                break
            
            # Polars doesn't support usecols, so we need to load all and filter
            df = pl.read_excel(file_path, sheet_name=sheet_name, engine=engine)
            logger.info(f"Fichier chargé avec Polars (engine={engine}): {file_path}, shape: {df.shape}")
            
            # Nettoyer les noms de colonnes problématiques
            df = _clean_column_names(df)
            
            # Filter columns if usecols specified
            if usecols:
                # Map original column names to cleaned names
                available_cols = df.columns
                cols_to_keep = []
                for orig_col in usecols:
                    # Try exact match first
                    if orig_col in available_cols:
                        cols_to_keep.append(orig_col)
                    else:
                        # Try to find by cleaned name
                        cleaned_orig = _clean_column_name(orig_col)
                        if cleaned_orig in available_cols:
                            cols_to_keep.append(cleaned_orig)
                if cols_to_keep:
                    df = df.select(cols_to_keep)
                    logger.info(f"Filtrage des colonnes: {len(cols_to_keep)} colonnes conservées")
            
            return df
        except Exception as e:
            logger.debug(f"Polars avec engine {engine} a échoué: {e}")
            continue
    
    # Utiliser pandas (nécessaire si header_row != 0 ou si Polars a échoué)
    logger.info(f"Chargement avec pandas (header_row={header_row}): {file_path}")
    try:
        # Si sheet_name est None, pandas peut retourner un dict si plusieurs feuilles
        # On spécifie sheet_name=0 pour prendre la première feuille
        # Load only specified columns if usecols provided
        # usecols should already contain original column names from the mapping
        if sheet_name is None:
            df_pandas = pd.read_excel(file_path, sheet_name=0, engine='openpyxl', header=header_row, usecols=usecols)
        else:
            df_pandas = pd.read_excel(file_path, sheet_name=sheet_name, engine='openpyxl', header=header_row, usecols=usecols)
        
        # Si pandas retourne un dict (peut arriver dans certains cas), prendre la première
        if isinstance(df_pandas, dict):
            logger.info(f"Plusieurs feuilles détectées, utilisation de la première: {list(df_pandas.keys())[0]}")
            df_pandas = list(df_pandas.values())[0]
        
        # Nettoyer les noms de colonnes avant conversion
        df_pandas.columns = [_clean_column_name(col) for col in df_pandas.columns]
        
        # Convertir les colonnes suspectes (codes, barcodes) en string AVANT la conversion Polars
        # Ces colonnes sont souvent numériques dans Excel mais doivent être traitées comme des strings
        # pour éviter les erreurs PyArrow lors de la conversion
        suspect_keywords = ['code', 'barcode', 'ean', 'gtin', 'sku', 'item', 'nbr', 'number']
        
        # Créer un dictionnaire pour stocker les colonnes à convertir
        columns_to_convert = {}
        
        for col in df_pandas.columns:
            col_lower = str(col).lower()
            if any(keyword in col_lower for keyword in suspect_keywords):
                columns_to_convert[col] = True
        
        if columns_to_convert:
            logger.info(f"Colonnes à convertir en string: {list(columns_to_convert.keys())}")
        
        # Convertir les colonnes suspectes en utilisant le type nullable string de pandas
        # Vectorized conversion (much faster than apply) - handle pd.NA properly
        for col in columns_to_convert.keys():
            # Convert to string handling pd.NA properly
            df_pandas[col] = df_pandas[col].apply(
                lambda x: str(int(x)) if pd.notna(x) and isinstance(x, (int, float)) and float(x).is_integer()
                else (str(x) if pd.notna(x) else None)
            )
            df_pandas[col] = df_pandas[col].astype('string')
        
        # Construire le DataFrame Polars manuellement pour éviter les problèmes PyArrow
        # Cette approche donne un contrôle total sur les types
        logger.debug("Construction manuelle du DataFrame Polars pour éviter les problèmes de type")
        data_dict = {}
        schema_overrides = {}
        
        for col in df_pandas.columns:
            if col in columns_to_convert:
                # Convert pd.NA to None before converting to list
                # The column was already converted to string type above, but may still contain pd.NA
                series = df_pandas[col]
                data_dict[col] = [_clean_na_values(x) if _clean_na_values(x) is None else str(_clean_na_values(x)) for x in series]
                schema_overrides[col] = pl.Utf8
            else:
                # Pour les autres colonnes, convertir en list et gérer les types mixtes
                # Polars peut avoir des problèmes avec les types mixtes, donc on convertit tout en string
                # sauf si c'est vraiment numérique
                try:
                    # Check if column has strings (vectorized check)
                    series = df_pandas[col]
                    # If dtype is object, might contain strings - check first non-null value
                    has_strings = series.dtype == 'object' and len(series.dropna()) > 0 and isinstance(series.dropna().iloc[0], str)
                    
                    if has_strings:
                        # Convert entire column to string handling pd.NA properly
                        data_dict[col] = [str(_clean_na_values(x)) if _clean_na_values(x) is not None else None for x in series]
                        schema_overrides[col] = pl.Utf8
                    else:
                        # Use as-is (purely numeric or other non-string types) - but convert pd.NA to None
                        data_dict[col] = [_clean_na_values(x) for x in series]
                except Exception as e:
                    # En cas d'erreur, forcer en string pour éviter les problèmes
                    logger.warning(f"Erreur lors de la conversion de la colonne '{col}', conversion en string: {e}")
                    data_dict[col] = [str(_clean_na_values(x)) if _clean_na_values(x) is not None else None for x in df_pandas[col]]
                    schema_overrides[col] = pl.Utf8
        
        # Créer le DataFrame Polars avec un schéma explicite
        # Utiliser strict=False pour permettre les types mixtes si nécessaire
        try:
            df = pl.DataFrame(data_dict, schema_overrides=schema_overrides, strict=False)
        except Exception as e:
            # Si ça échoue encore, forcer toutes les colonnes en string et convertir pd.NA
            logger.warning(f"Erreur lors de la création du DataFrame, conversion globale en string: {e}")
            for col in data_dict.keys():
                if col not in schema_overrides:
                    # Convert pd.NA to None and then to string
                    data_dict[col] = [str(_clean_na_values(x)) if _clean_na_values(x) is not None else None for x in data_dict[col]]
                    schema_overrides[col] = pl.Utf8
            df = pl.DataFrame(data_dict, schema_overrides=schema_overrides, strict=False)
        
        # Convertir les colonnes suspectes en string dans Polars (pour être sûr)
        # Cela évite les problèmes si certaines colonnes sont numériques mais doivent être traitées comme strings
        suspect_keywords = ['code', 'barcode', 'ean', 'gtin', 'sku', 'item', 'nbr', 'number']
        conversions = []
        for col in df.columns:
            col_lower = str(col).lower()
            if any(keyword in col_lower for keyword in suspect_keywords):
                # Convertir en string si ce n'est pas déjà le cas
                if df[col].dtype != pl.Utf8:
                    conversions.append(pl.col(col).cast(pl.Utf8).alias(col))
        
        if conversions:
            df = df.with_columns(conversions)
            converted_cols = [col for col in df.columns 
                            if any(kw in str(col).lower() for kw in suspect_keywords)]
            logger.debug(f"Colonnes converties en string: {converted_cols}")
        
        logger.info(f"Fichier chargé avec pandas: {file_path}, shape: {df.shape}")
        return df
    except Exception as e2:
        logger.error(f"Erreur lors du chargement de {file_path}: {e2}")
        raise


def _clean_na_values(value: Any) -> Any:
    """Convert pd.NA and other NA types to None for Polars compatibility."""
    import pandas as pd
    if pd.isna(value):
        return None
    # Check for pd.NA specifically (pandas nullable NA)
    try:
        if hasattr(pd, 'NA') and value is pd.NA:
            return None
    except:
        pass
    return value


def _clean_column_name(col_name: Any) -> str:
    """
    Nettoie un nom de colonne pour qu'il soit une string valide.
    """
    if col_name is None:
        return "Unnamed"
    if not isinstance(col_name, str):
        col_name = str(col_name)
    # Remplacer les caractères problématiques
    col_name = col_name.strip()
    if not col_name:
        return "Unnamed"
    return col_name


def _clean_column_names(df: pl.DataFrame) -> pl.DataFrame:
    """
    Nettoie tous les noms de colonnes du DataFrame.
    """
    new_names = {}
    for i, col in enumerate(df.columns):
        cleaned = _clean_column_name(col)
        if cleaned != col:
            new_names[col] = cleaned
    
    if new_names:
        df = df.rename(new_names)
        logger.debug(f"Noms de colonnes nettoyés: {new_names}")
    
    return df


def normalize_column_names(df: pl.DataFrame) -> pl.DataFrame:
    """
    Normalise les noms de colonnes (trim, gestion des espaces).
    """
    # Les noms de colonnes sont déjà nettoyés dans load_excel_file
    # Cette fonction peut être utilisée pour des normalisations supplémentaires si nécessaire
    return df


def clean_dataframe(df: pl.DataFrame, join_key: str) -> pl.DataFrame:
    """
    Nettoie le DataFrame : suppression des doublons sur la clé de jointure,
    gestion des valeurs nulles.
    
    Args:
        df: DataFrame à nettoyer
        join_key: Nom de la colonne clé de jointure
    
    Returns:
        DataFrame nettoyé
    """
    # Vérifier que la colonne existe
    if join_key not in df.columns:
        available_cols = ', '.join([f"'{col}'" for col in df.columns[:10]])  # Afficher les 10 premières
        if len(df.columns) > 10:
            available_cols += f", ... ({len(df.columns)} colonnes au total)"
        raise ValueError(
            f"Colonne de jointure '{join_key}' introuvable.\n"
            f"Colonnes disponibles: {available_cols}\n"
            f"Veuillez mettre à jour JOIN_KEY_STIBO ou JOIN_KEY_SAP dans config.py"
        )
    
    # Supprimer les doublons sur la clé de jointure (garder le premier)
    df_cleaned = df.unique(subset=[join_key], keep='first')
    
    # Convertir les valeurs vides en None
    # Attention : ne pas comparer les colonnes date/datetime/time avec des strings
    conversions = []
    for col in df_cleaned.columns:
        col_dtype = df_cleaned[col].dtype
        # Vérifier si c'est un type temporel (date, datetime, time)
        if col_dtype in (pl.Date, pl.Datetime, pl.Time, pl.Duration):
            # Pour les types temporels, on ne peut pas comparer avec "", on laisse tel quel
            # Les valeurs nulles sont déjà gérées par Polars
            continue
        elif col_dtype == pl.Utf8:
            # Pour les colonnes string, on peut comparer avec ""
            conversions.append(
                pl.when(pl.col(col) == "").then(None).otherwise(pl.col(col)).alias(col)
            )
        else:
            # Pour les autres types (numériques, bool, etc.), on laisse tel quel
            # Les valeurs vides sont généralement déjà None ou NaN
            continue
    
    if conversions:
        df_cleaned = df_cleaned.with_columns(conversions)
    
    logger.info(f"DataFrame nettoyé: {len(df)} -> {len(df_cleaned)} lignes")
    return df_cleaned


def load_and_prepare_data(
    excel_file_path: str,
    stibo_sheet: str,
    sap_sheet: str,
    join_key_stibo: str,
    join_key_sap: str,
    elist_sheet: Optional[str] = None,
    current_range_sheet: Optional[str] = None,
    stibo_header_row: int = 0,
    sap_header_row: int = 1,
    df_stibo_preloaded: Optional[pl.DataFrame] = None,
    df_sap_preloaded: Optional[pl.DataFrame] = None
) -> tuple[pl.DataFrame, pl.DataFrame, Optional[pl.DataFrame], Optional[pl.DataFrame]]:
    """
    Charge et prépare les données STIBO et SAP depuis un fichier Excel unique.
    
    Args:
        excel_file_path: Chemin vers le fichier Excel unique
        stibo_sheet: Nom de la feuille STIBO
        sap_sheet: Nom de la feuille SAP
        join_key_stibo: Nom de la colonne clé dans STIBO
        join_key_sap: Nom de la colonne clé dans SAP
        elist_sheet: Nom de la feuille Elist (optionnel, pour Brand SAP)
        current_range_sheet: Nom de la feuille Current Range (optionnel, pour Vendor SAP)
        stibo_header_row: Index de la ligne d'en-tête pour STIBO (0 = première ligne)
        sap_header_row: Index de la ligne d'en-tête pour SAP (1 = deuxième ligne)
    
    Returns:
        Tuple (df_stibo, df_sap, df_elist, df_current_range) nettoyés et prêts
    """
    # Use preloaded DataFrames if provided, otherwise load
    if df_stibo_preloaded is not None:
        df_stibo = df_stibo_preloaded
    else:
        df_stibo = load_excel_file(excel_file_path, sheet_name=stibo_sheet, header_row=stibo_header_row)
    
    if df_sap_preloaded is not None:
        df_sap = df_sap_preloaded
    else:
        df_sap = load_excel_file(excel_file_path, sheet_name=sap_sheet, header_row=sap_header_row)
    
    # Chargement des feuilles supplémentaires
    df_elist = None
    if elist_sheet:
        try:
            df_elist = load_excel_file(excel_file_path, sheet_name=elist_sheet)
            logger.info(f"Feuille Elist chargée: {df_elist.shape}")
        except Exception as e:
            logger.warning(f"Impossible de charger la feuille Elist: {e}")
    
    df_current_range = None
    if current_range_sheet:
        try:
            df_current_range = load_excel_file(excel_file_path, sheet_name=current_range_sheet)
            logger.info(f"Feuille Current Range chargée: {df_current_range.shape}")
        except Exception as e:
            logger.warning(f"Impossible de charger la feuille Current Range: {e}")
    
    # Normalisation des noms de colonnes
    df_stibo = normalize_column_names(df_stibo)
    df_sap = normalize_column_names(df_sap)
    if df_elist is not None:
        df_elist = normalize_column_names(df_elist)
    if df_current_range is not None:
        df_current_range = normalize_column_names(df_current_range)
    
    # Enrichir SAP avec Brand depuis Elist : EList prime si match, sinon on garde la Brand SAP
    if df_elist is not None and join_key_sap in df_sap.columns:
        elist_key_col = "Brakes Code" if "Brakes Code" in df_elist.columns else (join_key_sap if join_key_sap in df_elist.columns else None)
        if elist_key_col is not None and "Brand" in df_elist.columns:
            df_elist_brand = df_elist.select([
                pl.col(elist_key_col).cast(pl.Utf8).alias(join_key_sap),
                pl.col("Brand").alias("Brand_elist")
            ]).unique(subset=[join_key_sap], keep="first")
            df_sap = df_sap.with_columns(pl.col(join_key_sap).cast(pl.Utf8).alias(join_key_sap))
            df_sap = df_sap.join(df_elist_brand, on=join_key_sap, how="left")
            # Brand = EList si présente, sinon garder la valeur SAP
            if "Brand" in df_sap.columns:
                df_sap = df_sap.with_columns(
                    pl.coalesce(pl.col("Brand_elist"), pl.col("Brand")).alias("Brand")
                ).drop("Brand_elist")
            else:
                df_sap = df_sap.with_columns(pl.col("Brand_elist").alias("Brand")).drop("Brand_elist")
            logger.info("SAP Brand: EList when match, else keep SAP value")
    
    # Nettoyage
    df_stibo = clean_dataframe(df_stibo, join_key_stibo)
    df_sap = clean_dataframe(df_sap, join_key_sap)
    
    # Renommer la clé de jointure SAP pour faciliter le merge
    # On utilise toujours le nom STIBO comme clé unifiée
    unified_join_key = join_key_stibo
    if join_key_sap != join_key_stibo:
        df_sap = df_sap.rename({join_key_sap: unified_join_key})
    
    # S'assurer que les clés de jointure ont le même type dans les deux DataFrames
    # Convertir les deux en string pour éviter les erreurs de jointure
    stibo_key_type = df_stibo[unified_join_key].dtype
    sap_key_type = df_sap[unified_join_key].dtype
    
    if stibo_key_type != sap_key_type:
        logger.info(f"Types de clé de jointure différents: STIBO={stibo_key_type}, SAP={sap_key_type}. Conversion en string.")
        # Convertir les deux en string
        df_stibo = df_stibo.with_columns([
            pl.col(unified_join_key).cast(pl.Utf8).alias(unified_join_key)
        ])
        df_sap = df_sap.with_columns([
            pl.col(unified_join_key).cast(pl.Utf8).alias(unified_join_key)
        ])
    
    return df_stibo, df_sap, df_elist, df_current_range
