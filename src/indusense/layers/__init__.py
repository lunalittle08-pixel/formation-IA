"""Chargement des couches de l'architecture médaillon dans PostgreSQL.

    bronze : copie fidèle des sources (texte brut) + type inféré par colonne + empreinte de ligne,
             données personnelles pseudonymisées ;
    silver : données typées et contrôlées selon les contrats (lignes invalides en quarantaine) ;
    gold   : jeux prêts pour l'usage, définis par des requêtes SQL (configs/gold/*.sql).
"""
