"""
Configuration des attributs à comparer entre STIBO et SAP.
Approche configuration-driven pour faciliter l'ajout de nouveaux attributs.
"""

from typing import Callable, Optional, Dict, Any


def normalize_packsize(value: Any) -> Optional[str]:
    """
    Normalise le packsize au format #NBRx#NBR.
    Exemples: 
    - 1x24EACH -> 1x24
    - 2x6KG -> 2x6
    - 1x500ML -> 1x500
    - 2.6x10 -> 2.6x10 (garde les décimales)
    - 1X800G (KC1) -> 1x800 (enlève parenthèses et après)
    - 1 x 24 -> 1x24 (enlève espaces)
    """
    if value is None or (isinstance(value, float) and str(value) == 'nan'):
        return None
    
    value_str = str(value).strip()
    if not value_str:
        return None
    
    import re
    # Enlever tout ce qui est après une parenthèse ouvrante
    value_str = re.sub(r'\(.*$', '', value_str).strip()
    
    # Extraire le pattern #NBRx#NBR (avec décimales possibles)
    # Pattern: nombre (avec décimales) + x/X + nombre (avec décimales)
    match = re.search(r'(\d+\.?\d*)\s*[xX]\s*(\d+\.?\d*)', value_str, re.IGNORECASE)
    if match:
        num1 = match.group(1)
        num2 = match.group(2)
        # Format final: #NBRx#NBR (minuscule x, pas d'espaces)
        return f"{num1}x{num2}"
    
    return None


def normalize_barcode(value: Any) -> Optional[str]:
    """
    Normalise un code-barres en retirant tous les caractères non numériques.
    """
    if value is None or (isinstance(value, float) and str(value) == 'nan'):
        return None
    
    value_str = str(value).strip()
    if not value_str:
        return None
    
    import re
    normalized = re.sub(r'[^0-9]', '', value_str)
    return normalized if normalized else None


def normalize_text(value: Any) -> Optional[str]:
    """
    Normalise un texte : trim et conversion en minuscules.
    """
    if value is None or (isinstance(value, float) and str(value) == 'nan'):
        return None
    
    value_str = str(value).strip()
    return value_str.lower() if value_str else None


def normalize_exact(value: Any) -> Optional[str]:
    """
    Normalisation exacte : trim seulement.
    """
    if value is None or (isinstance(value, float) and str(value) == 'nan'):
        return None
    
    value_str = str(value).strip()
    return value_str if value_str else None


# Configuration des attributs à comparer
# Basé sur les colonnes réelles détectées dans les fichiers Excel
ATTRIBUTE_CONFIG = [
    {
        'attribute': 'product_name',
        'stibo_column': 'item description',
        'sap_column': 'Product Name',  # À vérifier dans SAP
        'stibo_normalizer': normalize_text,
        'sap_normalizer': normalize_text,
        'comparison_mode': 'case_insensitive',
        'description': 'Product Name'
    },
    {
        'attribute': 'packsize',
        'stibo_column': 'packsize',
        'sap_column': 'Product Packsize',  # À vérifier dans SAP
        'stibo_normalizer': normalize_packsize,
        'sap_normalizer': normalize_exact,
        'comparison_mode': 'exact',
        'description': 'Product Packsize'
    },
    {
        'attribute': 'split_case',
        'stibo_column': 'Split/Case',
        'sap_column': 'Product Split / Case',  # À vérifier dans SAP
        'stibo_normalizer': normalize_exact,
        'sap_normalizer': normalize_exact,
        'comparison_mode': 'exact',
        'description': 'Product Split / Case'
    },
    {
        'attribute': 'barcode_outer',
        'stibo_column': 'gtin outer',
        'sap_column': 'Barcode Outer',  # À vérifier dans SAP
        'stibo_normalizer': normalize_barcode,
        'sap_normalizer': normalize_barcode,
        'comparison_mode': 'exact',
        'description': 'Barcode Outer'
    },
    {
        'attribute': 'barcode_inner',
        'stibo_column': 'gtin inner',
        'sap_column': 'Barcode Inner',  # À vérifier dans SAP
        'stibo_normalizer': normalize_barcode,
        'sap_normalizer': normalize_barcode,
        'comparison_mode': 'exact',
        'description': 'Barcode Inner'
    },
    {
        'attribute': 'brand',
        'stibo_column': 'brand',
        'sap_column': 'Brand',  # À vérifier dans SAP
        'stibo_normalizer': normalize_text,
        'sap_normalizer': normalize_text,
        'comparison_mode': 'case_insensitive',
        'description': 'Brand'
    },
    {
        'attribute': 'vendor',
        'stibo_column': 'vendor product code',
        'sap_column': 'Vendor',  # À vérifier dans SAP
        'stibo_normalizer': normalize_text,
        'sap_normalizer': normalize_text,
        'comparison_mode': 'case_insensitive',
        'description': 'Vendor'
    }
]

# Colonnes contextuelles à conserver (non comparées mais présentes dans le résultat)
CONTEXT_COLUMNS = {
    'stibo': {
        'legally_package_split': 'legally packaged to be sold as split',
        # Ajouter d'autres colonnes contextuelles STIBO si nécessaire
    },
    'sap': {
        'discontinued': 'SAP MARA STATUS',  # À vérifier dans SAP
        'in_range': 'in range?',
        # Ajouter d'autres colonnes contextuelles SAP si nécessaire
    }
}

# Noms de colonnes pour la clé de jointure
JOIN_KEY_STIBO = 'itm_nbr'  # Item number dans STIBO
JOIN_KEY_SAP = 'Item Code'  # À vérifier dans SAP - peut être différent
