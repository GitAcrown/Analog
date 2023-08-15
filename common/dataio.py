import sqlite3
import discord
import os
from discord.ext import commands
from pathlib import Path
from typing import Union, Dict, List, Optional, Callable

OBJECT_PATH_STRUCTURE = {
        discord.User: "usr_{obj.id}",
        discord.Member: "mbr_{obj.id}_{obj.guild.id}",
        discord.Guild: "gld_{obj.id}",
        discord.TextChannel: "tch_{obj.id}",
        discord.Thread: "thr_{obj.id}",
        discord.VoiceChannel: "vch_{obj.id}"
    }

DB_TYPES = Union[discord.User, discord.Member, discord.Guild, discord.TextChannel, discord.Thread, discord.VoiceChannel, str, int]

class CogData:
    """Représente l'ensemble des données d'un Cog"""
    def __init__(self, cog_name: str) -> None:
        self.cog_name = cog_name
        self.cog_folder = Path(f"cogs/{self.cog_name}")
        
        # Cache des connexions aux bases de données
        self._db_cache = {}
        
    def __repr__(self) -> str:
        return f"<CogData {self.cog_name}>"
    
    def __del__(self) -> None:
        self.close_all_databases()
    
    # Assets -------------------
    
    def _get_assets(self) -> Path:
        path = self.cog_folder / "assets"
        return path
    
    @property
    def assets_path(self) -> Path:
        """Retourne le chemin vers le dossier assets du Cog

        :return: Chemin (Path) vers le dossier assets du Cog
        """
        return self._get_assets()
    
    # Databases ----------------

    def _get_sqlite_conn(self, obj: DB_TYPES) -> sqlite3.Connection:
        folder = self.cog_folder / "data"
        folder.mkdir(parents=True, exist_ok=True)
        db_path = folder / f"{_get_object_db_name(obj)}.db"
        return sqlite3.connect(db_path)
    
    def _load_database(self, obj: DB_TYPES, enable_row_factory: bool = True) -> sqlite3.Connection:
        """Charge une base de données pour un objet discord ou en crée une si elle n'existe pas encore"""
        if obj in self._db_cache:
            return self._db_cache[obj]
        conn = self._get_sqlite_conn(obj)
        if enable_row_factory:
            conn.row_factory = sqlite3.Row
        self._db_cache[obj] = conn
        return conn
    
    def _load_existing_databases(self, enable_row_factory: bool = True) -> Dict[str, sqlite3.Connection]:
        """Charge toutes les bases de données du Cog déjà existantes"""
        dbs = {}
        for db in self.cog_folder.glob("data/*.db"):
            conn = sqlite3.connect(db)
            if enable_row_factory:
                conn.row_factory = sqlite3.Row
            dbs[db.stem] = conn
        return dbs

    def get_database(self, obj: DB_TYPES) -> sqlite3.Connection:
        """Retourne une base de données pour un objet discord

        :param obj: Objet discord (User, Member, Guild, TextChannel) ou ID de l'objet
        :return: Connexion à la base de données du Cog
        """
        return self._load_database(obj)
    
    def get_all_databases(self) -> Dict[str, sqlite3.Connection]:
        """Retourne toutes les bases de données du Cog

        :return: Dictionnaire contenant toutes les connexions aux bases de données du Cog
        """
        return self._load_existing_databases()
    
    def close_database(self, obj: DB_TYPES) -> None:
        """Ferme une connexion à une base de données

        :param obj: Objet discord (User, Member, Guild, TextChannel) ou ID de l'objet
        """
        if obj in self._db_cache:
            self._db_cache[obj].close()
            del self._db_cache[obj]
            
    def close_all_databases(self) -> None:
        """Ferme toutes les connexions aux bases de données du Cog"""
        for conn in self._db_cache.values():
            conn.close()
        self._db_cache = {}
        
    # Operations ---------------------

    def fetchone(self, obj: DB_TYPES, query: str, *args) -> sqlite3.Row:
        """Exécute une requête SQL de recherche et retourne la première ligne du résultat

        :param obj: Objet discord (User, Member, Guild, TextChannel) ou ID de l'objet
        :param query: Requête SQL à exécuter
        :param *args: Arguments de la requête SQL
        :return: Première ligne du résultat
        """
        conn = self.get_database(obj)
        cursor = conn.cursor()
        cursor.execute(query, *args)
        result = cursor.fetchone()
        cursor.close()
        return result

    def fetchall(self, obj: DB_TYPES, query: str, *args) -> List[sqlite3.Row]:
        """Exécute une requête SQL de recherche et retourne toutes les lignes du résultat

        :param obj: Objet discord (User, Member, Guild, TextChannel) ou ID de l'objet
        :param query: Requête SQL à exécuter
        :param *args: Arguments de la requête SQL
        :return: Liste des lignes du résultat
        """
        conn = self.get_database(obj)
        cursor = conn.cursor()
        cursor.execute(query, *args)
        result = cursor.fetchall()
        cursor.close()
        return result
        
    def execute(self, obj: DB_TYPES, query: str, *args, commit: bool = True) -> None:
        """Exécute une requête SQL d'édition

        :param obj: Objet discord (User, Member, Guild, TextChannel) ou ID de l'objet
        :param query: Requête SQL à exécuter
        :param *args: Arguments de la requête SQL
        :param commit: Si True, commit les changements sur la base de données immédiatement (True par défaut)
        """
        conn = self.get_database(obj)
        cursor = conn.cursor()
        cursor.execute(query, *args)
        if commit:
            conn.commit()
        cursor.close()
        
    def executemany(self, obj: DB_TYPES, query: str, *args, commit: bool = True) -> None:
        """Exécute une requête SQL pour plusieurs lignes

        :param obj: Objet discord (User, Member, Guild, TextChannel) ou ID de l'objet
        :param query: Requête SQL à exécuter
        :param *args: Arguments de la requête SQL
        :param commit: Si True, commit les changements sur la base de données immédiatement (True par défaut)
        """
        conn = self.get_database(obj)
        cursor = conn.cursor()
        cursor.executemany(query, *args)
        if commit:
            conn.commit()
        cursor.close()
        
    def commit(self, obj: DB_TYPES) -> None:
        """Commit les changements sur une base de données

        :param obj: Objet discord (User, Member, Guild, TextChannel) ou ID de l'objet
        """
        conn = self.get_database(obj)
        conn.commit()
        
    # Utils --------------------------
    
    def estimate_size(self, obj: DB_TYPES) -> int:
        """Estime la taille d'une base de données

        :param obj: Objet discord (User, Member, Guild, TextChannel) ou ID de l'objet
        :return: Taille estimée de la base de données
        """
        conn = self.get_database(obj)
        cursor = conn.cursor()
        cursor.execute("SELECT page_count * page_size FROM pragma_page_count(), pragma_page_size()")
        result = cursor.fetchone()
        cursor.close()
        return result[0]
    
    
class UserDataEntry:
    """Représente un élément de stockage de données pour un utilisateur"""
    def __init__(self, user_id: int, table_name: str, table_desc: str, importance_level: int = 0):
        self.user_id = user_id # ID de l'utilisateur
        self.table_name = table_name # Nom de la table de données
        self.table_desc = table_desc # Description de la table (bref résumé de son contenu)
        
        # Niveau d'importance des données stockées dans cette table
        # 0 = Données insignifiantes (peuvent être supprimées sans conséquences)
        # 1 = Données importantes (leur suppression n'entraîne pas d'effets irréversibles)
        # 2 = Données critiques (leur suppression entraîne des effets irréversibles et affectera l'expérience de l'utilisateur)
        self.importance_level = importance_level
    
    def __repr__(self):
        return f"<UserDataElement user_id={self.user_id} table_name={self.table_name} table_desc={self.table_desc} importance_level={self.importance_level}>"
    
    def __str__(self) -> str:
        return self._get_string()
    
    def __eq__(self, other):
        return self.user_id == other.user_id and self.table_name == other.table_name
    
    def __hash__(self):
        return hash((self.user_id, self.table_name))
    
    def _get_string(self) -> str:
        """Retourne une chaîne de caractères représentant l'élément"""
        return f"**{self.table_name.capitalize()}** · {self.table_desc} [{self.importance_level}]"
    
    def to_dict(self) -> dict:
        """Retourne un dictionnaire contenant les informations de l'élément"""
        return {
            "user_id": self.user_id,
            "table_name": self.table_name,
            "table_desc": self.table_desc,
            "importance_level": self.importance_level
        }
        
    @classmethod
    def from_dict(cls, data: dict) -> 'UserDataEntry':
        """Crée un UserDataElement à partir d'un dictionnaire"""
        return cls(**data)

        
def get_cog_data(cog: Union[commands.Cog, str]) -> CogData:
    """Retourne les données d'un Cog

    :param cog: Cog ou nom du Cog
    :return: Données du Cog
    """
    name = cog if isinstance(cog, str) else cog.qualified_name
    return CogData(name.lower())

# Fonctions utilitaires -----------------------
        
def _get_object_db_name(obj: DB_TYPES) -> str:
    """Retourne un nom de base de données normalisé à partir d'un objet discord

    :param obj: Objet Discord commun (User, Member, Guild, TextChannel) ou ID brut de l'objet
    :return: Chemin vers l'objet discord
    """
    if isinstance(obj, (str, int)):
        return str(obj)
    return OBJECT_PATH_STRUCTURE[type(obj)].format(obj=obj)

def get_total_db_size() -> int:
    """Retourne la taille totale des bases de données se trouvant dans les dossiers /data des cogs"""
    total_size = 0
    for cogfile in os.listdir('cogs'):
        if 'data' in os.listdir(f'cogs/{cogfile}'):
            for dbfile in os.listdir(f'cogs/{cogfile}/data'):
                total_size += os.path.getsize(f'cogs/{cogfile}/data/{dbfile}')
    return total_size

def get_total_db_count() -> int:
    """Retourne le nombre total de bases de données se trouvant dans les dossiers /data des cogs"""
    total_count = 0
    for cogfile in os.listdir('cogs'):
        if 'data' in os.listdir(f'cogs/{cogfile}'):
            total_count += len(os.listdir(f'cogs/{cogfile}/data'))
    return total_count

# Gestion des données utilisateur -----------------------

def has_user_data(user_id: int, cogs: List[commands.Cog]) -> Dict[str, bool]:
    """Liste les cogs qui déclarent posséder des données utilisateur pour l'utilisateur spécifié

    :param user_id: ID de l'utilisateur
    :param cogs: Liste des cogs à vérifier
    :return: Dictionnaire indiquant pour chaque cog si l'utilisateur possède des données
    """
    data = {}
    for cog in cogs:
        if hasattr(cog, 'dataio_list_user_data'):
            data[cog.qualified_name] = cog.dataio_list_user_data(user_id) != [] #type: ignore
    return data

def get_user_data(user_id: int, cogs: List[commands.Cog]) -> Dict[str, List[UserDataEntry]]:
    """Retourne les données déclarées de l'utilisateur dans les cogs spécifiés

    :param user_id: ID de l'utilisateur
    :param cogs: Liste des cogs
    :return: Dictionnaire contenant les données de l'utilisateur pour chaque cog
    """
    data = {}
    for cog in cogs:
        if hasattr(cog, 'dataio_list_user_data'):
            entries = cog.dataio_list_user_data(user_id) #type: ignore
            if entries:
                data[cog.qualified_name] = entries
    return data

def wipe_user_data(user_id: int, cog: commands.Cog, table_names: List[str]) -> Dict[str, bool]:
    """Supprime les données de l'utilisateur dans les tables spécifiées

    :param user_id: ID de l'utilisateur
    :param cog: Cog contenant les tables
    :param table_names: Liste des tables à supprimer
    :return: Dictionnaire indiquant pour chaque table si la suppression a réussi
    """
    data = {}
    if hasattr(cog, 'dataio_wipe_user_data'):
        for table_name in table_names:
            data[table_name] = cog.dataio_wipe_user_data(user_id, table_name) #type: ignore
    return data
    
def extract_user_data(user_id: int, cog: commands.Cog, table_names: List[str]) -> Dict[str, Optional[dict]]:
    """Extrait les données de l'utilisateur dans les tables spécifiées

    :param user_id: ID de l'utilisateur
    :param cog: Cog contenant les tables
    :param table_names: Liste des tables à extraire
    :return: Dictionnaire contenant les données extraites pour chaque table
    """
    data = {}
    if hasattr(cog, 'dataio_extract_user_data'):
        for table_name in table_names:
            data[table_name] = cog.dataio_extract_user_data(user_id, table_name) #type: ignore
    return data
