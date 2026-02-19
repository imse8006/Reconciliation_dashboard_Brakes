# Dashboard de Réconciliation STIBO vs SAP

Dashboard Streamlit pour réconcilier les données de produits entre deux sources : STIBO et SAP.

## 🏗️ Architecture

La solution est organisée en modules modulaires :

- **`config.py`** : Configuration des attributs à comparer avec leurs normaliseurs
- **`loaders.py`** : Chargement et préprocessing des données Excel
- **`comparison_engine.py`** : Moteur de comparaison générique
- **`app.py`** : Interface Streamlit principale

## 📋 Prérequis

- Python 3.8+
- Les fichiers Excel :
  - `SAP_extract.xlsx`
  - `STIBO_extract.xlsx`

## 🚀 Installation

1. Créer un environnement virtuel (recommandé) :
```bash
# Sur Windows
py -m venv venv

# Activer l'environnement virtuel (PowerShell)
.\venv\Scripts\Activate.ps1

# Activer l'environnement virtuel (CMD)
venv\Scripts\activate.bat
```

2. Installer les dépendances :
```bash
pip install -r requirements.txt
```

3. Vérifier que les fichiers Excel sont présents dans le répertoire du projet :
   - `SAP_extract.xlsx`
   - `STIBO_extract.xlsx`

## 💻 Utilisation

**Important** : Assurez-vous que l'environnement virtuel est activé avant de lancer l'application.

Lancer l'application Streamlit :

```bash
streamlit run app.py
```

L'application s'ouvrira automatiquement dans votre navigateur par défaut (généralement sur `http://localhost:8501`).

### Workflow

1. **Charger les données** : Cliquer sur "🔄 Charger les données" dans la barre latérale
2. **Consulter les KPIs** : Les indicateurs clés s'affichent en haut de la page
3. **Filtrer** : Utiliser les filtres pour affiner les résultats
4. **Analyser** : Consulter le tableau de comparaison avec formatage conditionnel
5. **Drill-down** : Sélectionner un SKU pour voir toutes ses comparaisons
6. **Exporter** : Exporter les incohérences ou la vue filtrée en Excel

## ⚙️ Configuration

### Système de mapping des colonnes

Le système utilise un fichier `column_mapping.json` pour détecter automatiquement les colonnes. Cela permet de gérer différents noms de colonnes selon le marché.

#### Structure du fichier `column_mapping.json`

La structure est organisée par marché, puis par source :

```json
{
  "join_key": {
    "Brakes": {
      "stibo": ["itm_nbr", "SKU", "Item Number"],
      "sap": ["Item Code", "SKU", "itm_nbr"]
    }
  },
  "attributes": {
    "product_name": {
      "Brakes": {
        "stibo": ["item description", "Product Name"],
        "sap": ["Product Name", "item description"]
      }
    }
  }
}
```

Cette structure permet d'ajouter facilement d'autres marchés (ex: "France", "UK") avec leurs propres mappings. Le système cherchera automatiquement le premier nom de colonne qui correspond dans chaque liste.

#### Ajouter un nouvel attribut

Pour ajouter un nouvel attribut, modifier `column_mapping.json` :

```json
{
  "attributes": {
    "nouvel_attribut": {
      "Brakes": {
        "stibo": ["Nom Colonne STIBO 1", "Nom Colonne STIBO 2"],
        "sap": ["Nom Colonne SAP 1", "Nom Colonne SAP 2"]
      }
    }
  }
}
```

Puis ajouter les normaliseurs dans `mapping_loader.py` dans la fonction `build_attribute_config_from_mapping()`.

#### Ajouter un nouveau marché

Pour ajouter un nouveau marché (ex: "France"), ajouter une nouvelle entrée dans chaque section :

```json
{
  "join_key": {
    "Brakes": { ... },
    "France": {
      "stibo": ["code_produit", "SKU"],
      "sap": ["Code Article", "SKU"]
    }
  },
  "attributes": {
    "product_name": {
      "Brakes": { ... },
      "France": {
        "stibo": ["nom_produit", "Description"],
        "sap": ["Nom Produit", "Libellé"]
      }
    }
  }
}
```

### Normaliseurs disponibles

- `normalize_packsize` : Retire les suffixes alphabétiques (ex: 1x24EACH → 1x24)
- `normalize_barcode` : Retire tous les caractères non numériques
- `normalize_text` : Trim + conversion en minuscules
- `normalize_exact` : Trim seulement

### Modes de comparaison

- `exact` : Comparaison exacte des valeurs normalisées
- `case_insensitive` : Comparaison insensible à la casse

## 📊 Format de sortie

Le tableau de comparaison est en format "long" avec les colonnes :

- `sku` : Identifiant du produit
- `attribute` : Nom de l'attribut comparé
- `stibo_value_raw` : Valeur brute STIBO
- `sap_value_raw` : Valeur brute SAP
- `stibo_value_norm` : Valeur normalisée STIBO
- `sap_value_norm` : Valeur normalisée SAP
- `status` : Statut de la comparaison (MATCH, MISMATCH, MISSING_STIBO, MISSING_SAP, BOTH_MISSING)
- `diff_type` : Type de différence (EXACT, FORMAT_ONLY, REAL_DIFF)

## 🔍 Statuts de comparaison

- **MATCH** : Les valeurs correspondent après normalisation
- **MISMATCH** : Les valeurs diffèrent
- **MISSING_STIBO** : Valeur manquante dans STIBO
- **MISSING_SAP** : Valeur manquante dans SAP
- **BOTH_MISSING** : Valeur manquante dans les deux sources

## 📝 Notes techniques

- Utilise **Polars** pour le traitement des données (performant et efficace)
- Conversion vers Pandas uniquement pour l'affichage Streamlit
- Approche configuration-driven : facile d'ajouter de nouveaux attributs
- Format long : une ligne par SKU × attribut pour faciliter l'analyse

## 🐛 Dépannage

### Erreur : Colonne introuvable

Si une colonne n'est pas trouvée :
1. Vérifier que le nom de colonne est présent dans `column_mapping.json`
2. Ajouter le nom exact (avec la casse et les espaces) dans la liste des noms possibles
3. Vérifier les colonnes disponibles dans l'expander "Colonnes disponibles pour débogage" en cas d'erreur

### Erreur : Clé de jointure introuvable

1. Vérifier que les noms de colonnes dans `column_mapping.json` sous `join_key` correspondent aux colonnes réelles
2. Ajouter d'autres variantes possibles dans les listes (ex: "SKU", "sku", "Item Code", etc.)
3. Le système cherchera automatiquement le premier nom qui correspond
