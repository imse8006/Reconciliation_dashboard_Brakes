"""
Module pour charger et utiliser le mapping des colonnes depuis un fichier JSON.
Permet de gérer plusieurs noms possibles pour chaque colonne.
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
import logging

logger = logging.getLogger(__name__)


def load_column_mapping(mapping_file: str = "column_mapping.json") -> Dict:
    """
    Charge le fichier de mapping des colonnes.
    
    Args:
        mapping_file: Chemin vers le fichier JSON de mapping
    
    Returns:
        Dictionnaire de mapping
    """
    mapping_path = Path(mapping_file)
    
    if not mapping_path.exists():
        logger.warning(f"Fichier de mapping {mapping_file} introuvable. Utilisation des valeurs par défaut.")
        return _get_default_mapping()
    
    try:
        with open(mapping_path, 'r', encoding='utf-8') as f:
            mapping = json.load(f)
        logger.info(f"Mapping chargé depuis {mapping_file}")
        return mapping
    except Exception as e:
        logger.error(f"Erreur lors du chargement du mapping: {e}")
        return _get_default_mapping()


def find_column_name(possible_names: List[str], available_columns: List[str], case_sensitive: bool = False) -> Optional[str]:
    """
    Trouve le premier nom de colonne qui correspond dans la liste des colonnes disponibles.
    
    Args:
        possible_names: Liste des noms possibles à chercher
        available_columns: Liste des colonnes disponibles dans le DataFrame
        case_sensitive: Si False, la recherche est insensible à la casse
    
    Returns:
        Le nom de colonne trouvé, ou None si aucun ne correspond
    """
    if case_sensitive:
        available_set = set(available_columns)
        for name in possible_names:
            if name in available_set:
                return name
    else:
        available_lower = {col.lower(): col for col in available_columns}
        for name in possible_names:
            name_lower = name.lower()
            if name_lower in available_lower:
                return available_lower[name_lower]
    
    return None


def detect_columns(
    mapping: Dict,
    stibo_columns: List[str],
    sap_columns: List[str],
    market: str = "Brakes"
) -> Tuple[Dict[str, str], Dict[str, Dict[str, str]], Dict[str, Dict[str, str]]]:
    """
    Détecte automatiquement les colonnes correspondantes dans STIBO et SAP.
    
    Args:
        mapping: Dictionnaire de mapping chargé depuis JSON
        stibo_columns: Liste des colonnes disponibles dans STIBO
        sap_columns: Liste des colonnes disponibles dans SAP
        market: Nom du marché (ex: "Brakes", "France", "UK")
    
    Returns:
        Tuple (join_keys, attribute_columns, context_columns)
        - join_keys: {'stibo': 'nom_colonne', 'sap': 'nom_colonne'}
        - attribute_columns: {'attribute_name': {'stibo': 'nom_colonne', 'sap': 'nom_colonne'}}
        - context_columns: {'stibo': {'key': 'nom_colonne'}, 'sap': {'key': 'nom_colonne'}}
    """
    detected = {
        'join_keys': {},
        'attribute_columns': {},
        'context_columns': {'stibo': {}, 'sap': {}}
    }
    
    # Vérifier que le marché existe dans le mapping
    if market not in mapping.get('join_key', {}):
        available_markets = list(mapping.get('join_key', {}).keys())
        raise ValueError(
            f"Marché '{market}' introuvable dans le mapping. "
            f"Marchés disponibles: {available_markets}"
        )
    
    # Détecter les clés de jointure pour le marché spécifié
    join_key_mapping = mapping.get('join_key', {}).get(market, {})
    
    stibo_join = find_column_name(join_key_mapping.get('stibo', []), stibo_columns)
    sap_join = find_column_name(join_key_mapping.get('sap', []), sap_columns)
    
    if not stibo_join:
        raise ValueError(
            f"Aucune clé de jointure STIBO trouvée pour le marché '{market}'. "
            f"Colonnes disponibles: {stibo_columns[:10]}..."
        )
    if not sap_join:
        raise ValueError(
            f"Aucune clé de jointure SAP trouvée pour le marché '{market}'. "
            f"Colonnes disponibles: {sap_columns[:10]}..."
        )
    
    detected['join_keys'] = {'stibo': stibo_join, 'sap': sap_join}
    logger.info(f"Clés de jointure détectées pour '{market}': STIBO='{stibo_join}', SAP='{sap_join}'")
    
    # Détecter les colonnes d'attributs pour le marché spécifié
    attributes_mapping = mapping.get('attributes', {})
    
    # SAP columns that are enriched from other sheets (not on the SAP sheet itself)
    # e.g. vendor = "Ult Ven Name" comes from Current Range sheet
    sap_enriched_fallback = {'vendor': 'Ult Ven Name'}
    
    for attr_name, attr_markets in attributes_mapping.items():
        market_mapping = attr_markets.get(market, {})
        
        stibo_col = find_column_name(market_mapping.get('stibo', []), stibo_columns)
        sap_col = find_column_name(market_mapping.get('sap', []), sap_columns)
        
        # If SAP column not on sheet but added by enrichment (e.g. from Current Range), use mapping name
        if sap_col is None and attr_name in sap_enriched_fallback:
            mapping_sap_candidates = market_mapping.get('sap', [])
            fallback_name = sap_enriched_fallback[attr_name]
            if mapping_sap_candidates and (mapping_sap_candidates[0] == fallback_name or mapping_sap_candidates[0].strip() == fallback_name):
                sap_col = mapping_sap_candidates[0].strip()  # use exact name from mapping
                logger.debug(f"Attribut '{attr_name}' SAP: colonne '{sap_col}' (enrichie depuis Current Range)")
        
        if stibo_col or sap_col:
            detected['attribute_columns'][attr_name] = {
                'stibo': stibo_col,
                'sap': sap_col
            }
            logger.debug(f"Attribut '{attr_name}' pour '{market}': STIBO='{stibo_col}', SAP='{sap_col}'")
        else:
            logger.warning(f"Attribut '{attr_name}' non trouvé pour le marché '{market}'")
    
    # Détecter les colonnes contextuelles pour le marché spécifié
    context_mapping = mapping.get('context_columns', {}).get(market, {})
    
    for source in ['stibo', 'sap']:
        source_mapping = context_mapping.get(source, {})
        for ctx_key, possible_names in source_mapping.items():
            col = find_column_name(possible_names, stibo_columns if source == 'stibo' else sap_columns)
            if col:
                detected['context_columns'][source][ctx_key] = col
                logger.debug(f"Colonne contextuelle {source}.{ctx_key} pour '{market}': '{col}'")
    
    return detected['join_keys'], detected['attribute_columns'], detected['context_columns']


def build_attribute_config_from_mapping(
    detected_attributes: Dict[str, Dict[str, Optional[str]]],
    normalizers: Optional[Dict[str, Any]] = None
) -> List[Dict]:
    """
    Construit la configuration des attributs à partir du mapping détecté.
    
    Args:
        detected_attributes: Dictionnaire des attributs détectés
        normalizers: Dictionnaire optionnel des fonctions de normalisation personnalisées
    
    Returns:
        Liste de configuration d'attributs au format ATTRIBUTE_CONFIG
    """
    from config import (
        normalize_packsize, normalize_barcode, normalize_text, normalize_exact
    )
    
    if normalizers is None:
        normalizers = {}
    
    # Mapping des normaliseurs par défaut
    default_normalizers = {
        'product_name': {'stibo': normalize_text, 'sap': normalize_text, 'mode': 'case_insensitive'},
        'packsize': {'stibo': normalize_packsize, 'sap': normalize_packsize, 'mode': 'exact'},
        'split_case': {'stibo': normalize_exact, 'sap': normalize_exact, 'mode': 'exact'},
        'barcode_outer': {'stibo': normalize_barcode, 'sap': normalize_barcode, 'mode': 'exact'},
        'barcode_inner': {'stibo': normalize_barcode, 'sap': normalize_barcode, 'mode': 'exact'},
        'brand': {'stibo': normalize_text, 'sap': normalize_text, 'mode': 'case_insensitive'},
        'vendor': {'stibo': normalize_text, 'sap': normalize_text, 'mode': 'case_insensitive'},
    }
    
    attribute_config = []
    
    for attr_name, cols in detected_attributes.items():
        if not cols.get('stibo') and not cols.get('sap'):
            continue  # Ignorer si aucune colonne trouvée
        
        normalizer_config = default_normalizers.get(attr_name, {
            'stibo': normalize_exact,
            'sap': normalize_exact,
            'mode': 'exact'
        })
        
        # Utiliser les normaliseurs personnalisés si fournis
        stibo_norm = normalizers.get(f'{attr_name}_stibo', normalizer_config['stibo'])
        sap_norm = normalizers.get(f'{attr_name}_sap', normalizer_config['sap'])
        
        attr_config = {
            'attribute': attr_name,
            'stibo_column': cols.get('stibo'),
            'sap_column': cols.get('sap'),
            'stibo_normalizer': stibo_norm,
            'sap_normalizer': sap_norm,
            'comparison_mode': normalizer_config['mode'],
            'description': attr_name.replace('_', ' ').title()
        }
        
        attribute_config.append(attr_config)
    
    return attribute_config


def _get_default_mapping() -> Dict:
    """Retourne un mapping par défaut si le fichier JSON n'existe pas."""
    return {
        "join_key": {
            "Brakes": {
                "stibo": ["itm_nbr", "SKU", "Item Number"],
                "sap": ["Item Code", "SKU", "itm_nbr"]
            }
        },
        "attributes": {},
        "context_columns": {
            "Brakes": {
                "stibo": {},
                "sap": {}
            }
        }
    }
