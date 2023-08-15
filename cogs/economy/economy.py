import json
import logging
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Literal

import discord
from discord import app_commands
from discord.ext import commands
from tabulate import tabulate

from sqids import Sqids

from common import dataio
from common.utils import pretty, fuzzy

logger = logging.getLogger(f'Analog.{__name__.capitalize()}')

DEFAULT_CONFIG = {
    'Currency': '✦',
    'DailyAmount': 200,
    'DailyLimit': 5000,
    'DefaultBalance':  100
}

TRANSACTION_EXPIRATION_DELAY = 86400 * 14 # 14 days
TRANSACTION_CLEANUP_INTERVAL = 3600 # 1 hour

# UI ==========================================================================

class TransactionsHistoryView(discord.ui.View):
    def __init__(self, interaction: discord.Interaction, account: 'Account'):
        super().__init__(timeout=60)
        self.initial_interaction = interaction
        self.account = account
        
        self.transactions : List['Transaction'] = account.get_transactions()
        self.current_page = 0
        self.display_type = 'reason'
        self.pages : List[discord.Embed] = self.get_pages(self.display_type)
        
        self.previous.disabled = True
        if len(self.pages) <= 1:
            self.next.disabled = True
        
        self.message : discord.InteractionMessage
        
    async def interaction_check(self, interaction: discord.Interaction):
        return interaction.user == self.initial_interaction.user
    
    async def on_timeout(self) -> None:
        await self.message.edit(view=self.clear_items())
        
    def get_pages(self, display: Literal['reason', 'ids']):
        embeds = []
        tabl = []
        coltype = "Raison" if display == 'reason' else "Identifiant"
        for trs in self.transactions:
            if len(tabl) < 20:
                if display == 'reason':
                    tabl.append((f"{trs.frelative}", f"{trs.amount:+}", f"{pretty.troncate_text(trs.reason, 50)}"))
                else:
                    tabl.append((f"{trs.frelative}", f"{trs.amount:+}", f"{trs.id}"))
            else:
                em = discord.Embed(color=0x2b2d31, description=pretty.codeblock(tabulate(tabl, headers=("Date", "Montant", coltype))))
                em.set_author(name=f"Historique des transactions · {self.account.owner.display_name}", icon_url=self.account.owner.display_avatar.url)
                em.set_footer(text=f"{len(self.transactions)} transactions sur les {int(TRANSACTION_EXPIRATION_DELAY / 86400)} derniers jours")
                embeds.append(em)
                tabl = []
        
        if tabl:
            em = discord.Embed(color=0x2b2d31, description=pretty.codeblock(tabulate(tabl, headers=("Date", "Montant", coltype)))) 
            em.set_author(name=f"Historique des transactions · {self.account.owner.display_name}", icon_url=self.account.owner.display_avatar.url)
            em.set_footer(text=f"{len(self.transactions)} transactions sur les {int(TRANSACTION_EXPIRATION_DELAY / 86400)} derniers jours")
            embeds.append(em)
            
        return embeds
    
    async def start(self):
        if self.pages:
            await self.initial_interaction.response.send_message(embed=self.pages[self.current_page], view=self)
        else:
            await self.initial_interaction.response.send_message("**Erreur** · Cet historique de transactions est vide.")
            self.stop()
            return self.clear_items()
        self.message = await self.initial_interaction.original_response()
        
    async def buttons_logic(self, interaction: discord.Interaction):
        self.previous.disabled = self.current_page == 0
        self.next.disabled = self.current_page + 1 >= len(self.pages)
        self.switch_display.label = "Aff. Raisons" if self.display_type == 'ids' else "Aff. IDs"
        await interaction.message.edit(view=self) #type: ignore
        
    @discord.ui.button(label="<", style=discord.ButtonStyle.blurple)
    async def previous(
        self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = max(0, self.current_page - 1)
        await self.buttons_logic(interaction)
        await interaction.response.edit_message(embed=self.pages[self.current_page])
        
    @discord.ui.button(label="Aff. IDs", style=discord.ButtonStyle.gray)
    async def switch_display(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.display_type = 'reason' if self.display_type == 'ids' else 'ids'
        self.pages = self.get_pages(self.display_type)
        await self.buttons_logic(interaction)
        await interaction.response.edit_message(embed=self.pages[self.current_page])

    @discord.ui.button(label=">", style=discord.ButtonStyle.blurple)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = min(len(self.pages) - 1, self.current_page + 1)
        await self.buttons_logic(interaction)
        await interaction.response.edit_message(embed=self.pages[self.current_page])
    
    @discord.ui.button(label="Fermer", style=discord.ButtonStyle.red)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await self.message.delete()
        
class ConfirmationView(discord.ui.View):
    """Ajoute un bouton de confirmation et d'annulation à un message"""
    def __init__(self, *, custom_labels: tuple[str, str] | None = None, timeout: float | None = 60):
        super().__init__(timeout=timeout)
        self.value = None
        
        if custom_labels is None:
            custom_labels = ('Confirmer', 'Annuler')
        self.confirm.label = custom_labels[0]
        self.cancel.label = custom_labels[1]
        
    @discord.ui.button(style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        self.stop()
        
    @discord.ui.button(style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        self.stop()
    
        
# CLASSES =====================================================================

class Account:
    """Représente le compte bancaire d'un utilisateur sur un serveur"""
    def __init__(self, cog: 'Economy', user: discord.Member):
        self.__cog = cog
        self.owner = user
        self.guild = user.guild
        
        self.__create_if_not_exists()
        
    def __repr__(self):
        return f"<Account user={self.owner}>"
    
    def __str__(self):
        return self.owner
    
    def __int__(self):
        return self.balance

    def __eq__(self, other: Any):
        if isinstance(other, Account):
            return self.owner == other.owner
        elif isinstance(other, discord.Member):
            return self.owner == other
        else:
            return False
        
    def __create_if_not_exists(self):
        self.__cog.data.execute(self.guild, "INSERT OR IGNORE INTO accounts VALUES (?, ?)", (self.owner.id, int(self.__cog.get_guild_config(self.guild)['DefaultBalance'])))
        
    def _get_balance(self) -> int:
        return self.__cog.data.fetchone(self.guild, "SELECT balance FROM accounts WHERE user_id = ?", (self.owner.id,))['balance']
        
    def _set_balance(self, value: int, *, reason: str = '') -> 'Transaction':
        current = self._get_balance()
        delta = value - current
        self.__cog.data.execute(self.guild, "UPDATE accounts SET balance = ? WHERE user_id = ?", (value, self.owner.id))
        return Transaction(self.__cog, self.owner, delta, reason=reason)
    
    @property
    def balance(self) -> int:
        """Renvoie le solde du compte"""
        return self._get_balance()
    
    @property
    def display_balance(self) -> str:
        """Retourne une représentation textuelle du solde du compte avec le symbole de la monnaie du serveur"""
        return f'{self.balance}{self.__cog.get_currency(self.guild)}'

    def set(self, value: int, *, reason: str = '') -> 'Transaction':
        """Modifie le solde du compte"""
        if value < 0:
            raise ValueError('Le solde du compte ne peut pas être négatif')
        
        return self._set_balance(value, reason=reason)
    
    def deposit(self, amount: int, *, reason: str = '') -> 'Transaction':
        """Ajoute de l'argent au compte"""
        if amount < 0:
            raise ValueError('Le montant ne peut pas être négatif')
        
        return self._set_balance(self._get_balance() + amount, reason=reason)
    
    def withdraw(self, amount: int, *, reason: str = '') -> 'Transaction':
        """Retire de l'argent du compte"""
        if amount < 0:
            amount = abs(amount)
            
        return self._set_balance(self._get_balance() - amount, reason=reason)
    
    def reset(self) -> 'Transaction':
        """Réinitialise le solde du compte"""
        default_balance = int(self.__cog.get_guild_config(self.guild)['DefaultBalance'])
        return self._set_balance(int(default_balance), reason='Réinitialisation du compte')

    def cancel(self, transaction: 'Transaction') -> 'Transaction':
        """Annule les effets d'une transaction en créant une transaction inverse"""
        if transaction not in self.get_transactions(limit=None):
            raise ValueError('La transaction n\'est pas liée à ce compte')
            
        return self._set_balance(self._get_balance() - transaction.amount, reason=f'Annule {transaction.id}')
    
    # Functions ----------------------------------------------------------------
    
    def get_transactions(self, limit: int | None = 5) -> List['Transaction']:
        """Renvoie les dernières transactions du compte"""
        if limit:
            rows = self.__cog.data.fetchall(self.guild, "SELECT * FROM transactions WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?", (self.owner.id, limit))
        else:
            rows = self.__cog.data.fetchall(self.guild, "SELECT * FROM transactions WHERE user_id = ? ORDER BY timestamp DESC", (self.owner.id,))
        return [Transaction(self.__cog, self.owner, row['amount'], reason=row['reason'], timestamp=row['timestamp']) for row in rows]
    
    def get_transaction(self, id: str) -> 'Transaction':
        """Renvoie une transaction du compte"""
        return Transaction.from_id(self.__cog, self.guild, id)
    
    def balance_variation(self, since: datetime | float) -> int:
        """Renvoie la variation du solde du compte depuis une date"""
        if isinstance(since, datetime):
            since = since.timestamp()
        r = self.__cog.data.fetchone(self.guild, "SELECT SUM(amount) FROM transactions WHERE user_id = ? AND timestamp > ?", (self.owner.id, since))['SUM(amount)']
        return int(r) if r else 0
    
    # Utils --------------------------------------------------------------------
    
    @property
    def embed(self) -> discord.Embed:
        """Retourne une embed d'information sur le compte"""
        em = discord.Embed(title=f"Compte Bancaire · *{self.owner.display_name}*", color=0x2b2d31)
        em.add_field(name="Solde", value=pretty.codeblock(self.display_balance))

        balancevar = self.balance_variation(datetime.now() - timedelta(hours=24))
        em.add_field(name="Var. s/ 24h", value=pretty.codeblock(f'{balancevar:+}', lang='diff'))
        
        rank = self.__cog.get_account_rank(self)
        em.add_field(name="Rang", value=pretty.codeblock(f'#{rank}'))
        
        transactions = self.get_transactions()
        if transactions:
            txt = '\n'.join([str(tr) for tr in transactions])
            em.add_field(name="Dernières transactions", value=pretty.codeblock(txt, lang='diff'), inline=False)
    
        em.set_thumbnail(url=self.owner.display_avatar.url)
        return em
    
class Transaction:
    """Represente une transaction économique"""
    def __init__(self, cog: 'Economy', user: discord.Member, amount: int, *, reason: str = '', timestamp: float = 0):
        self.__cog = cog
        self.user = user
        self.guild = user.guild
        self.amount = amount
        self.reason = reason
        self.timestamp = timestamp or datetime.now().timestamp()
        
        self.id = self.__get_id()
        self.__create_if_not_exists()

    def __get_id(self) -> str:
        s = Sqids()
        return s.encode([int(self.timestamp), self.user.id, abs(self.amount)])
    
    def __repr__(self):
        return f"<Transaction id={self.id}>"
    
    def __str__(self):
        return f'{self.amount:+}' + (f' · {pretty.troncate_text(self.reason, 50)}' if self.reason else '')
    
    def __eq__(self, __value: object) -> bool:
        if isinstance(__value, Transaction):
            return self.id == __value.id
        elif isinstance(__value, str):
            return self.id == __value
        else:
            return False
        
    def __hash__(self) -> int:
        return hash(self.id)
    
    def __create_if_not_exists(self):
        self.__cog.data.execute(self.guild, "INSERT OR IGNORE INTO transactions VALUES (?, ?, ?, ?, ?)", (self.id, self.timestamp, self.amount, self.reason, self.user.id))
        
        if self.__cog.last_cleanup + TRANSACTION_CLEANUP_INTERVAL < datetime.now().timestamp():
            self.__cog.cleanup_transactions(self.guild)
            self.__cog.last_cleanup = datetime.now().timestamp()
                
    @classmethod
    def from_id(cls, cog: 'Economy', guild: discord.Guild, id: str):
        row = cog.data.fetchone(guild, "SELECT * FROM transactions WHERE id = ?", (id,))
        user = guild.get_member(row['user_id'])
        if not user:
            raise ValueError('L\'utilisateur n\'est pas présent sur le serveur')
        
        return cls(cog, user, row['amount'], reason=row['reason'], timestamp=row['timestamp'])
    
    # Functions ----------------------------------------------------------------
    
    def update(self, reason: str) -> None:
        """Modifie la raison de la transaction"""
        self.__cog.data.execute(self.guild, "UPDATE transactions SET reason = ? WHERE id = ?", (reason, self.id))
        
    def delete(self) -> None:
        """Supprime la transaction"""
        self.__cog.data.execute(self.guild, "DELETE FROM transactions WHERE id = ?", (self.id,))
    
    # Time utils ---------------------------------------------------------------
    
    @property
    def ftime(self) -> str:
        """Renvoie l'heure de la transaction au format HH:MM"""
        return datetime.fromtimestamp(self.timestamp).strftime('%H:%M')
    
    @property
    def fdate(self) -> str:
        """Renvoie la date de la transaction au format JJ/MM/AAAA"""
        return datetime.fromtimestamp(self.timestamp).strftime('%d/%m/%Y')
    
    @property
    def frelative(self) -> str:
        """Renvoie la date de la transaction dans un format court et de manière relative"""
        today = datetime.now().date()
        if datetime.fromtimestamp(self.timestamp).date() == today:
            return f"{self.ftime}"
        elif datetime.fromtimestamp(self.timestamp).year == today.year:
            return datetime.fromtimestamp(self.timestamp).strftime('%d/%m')
        else:
            return f'{self.fdate} {self.ftime}'
    
    @property
    def fdiscord(self) -> str:
        """Renvoie la date de la transaction au format Discord en temps relatif"""
        return f'<t:{int(self.timestamp)}:R>'
    
    # Display utils ------------------------------------------------------------
    
    @property
    def display_amount(self) -> str:
        """Retourne une représentation textuelle du montant de la transaction avec le symbole de la monnaie du serveur"""
        return f'{self.amount}{self.__cog.get_currency(self.guild)}'
    
    @property
    def embed(self) -> discord.Embed:
        """Retourne une embed d'information sur la transaction"""
        em = discord.Embed(title=f"Transaction · **`{self.id}`**", color=0x2b2d31)
        em.add_field(name="Compte", value=self.user.mention)
        em.add_field(name="Montant", value=pretty.codeblock(self.display_amount, lang='diff'))
        em.add_field(name="Date", value=self.fdiscord)
        if self.reason:
            em.add_field(name="Raison", value=f'`{self.reason}`')
        em.set_thumbnail(url=self.user.display_avatar.url)
        return em
    
class Condition:
    """Représente une condition d'action ou de transaction"""
    def __init__(self, cog: 'Economy', name: str, obj: discord.Member | discord.TextChannel | discord.Thread, value: Any = None, *, default_value: Any = None):
        self.__cog = cog
        self.name = name
        self.obj = obj
        self._value = value if value else default_value
        
        self.guild = obj.guild
        self.id = f'{self.name}@{self.obj.id}'
        self.__load()
        
    def __repr__(self):
        return f"<Condition object={self.obj} name={self.name} value={self._value}>"
    
    def __str__(self):
        return self.id
    
    def __load(self):
        r = self.__cog.data.fetchone(self.guild, "SELECT value FROM conditions WHERE id = ?", (self.id,))
        if r:
            self._value = json.loads(r['value'])
        else:
            self.__cog.data.execute(self.guild, "INSERT OR IGNORE INTO conditions VALUES (?, ?)", (self.id, self._serialized))
            
    @property
    def _serialized(self) -> str:
        return json.dumps(self._value)
    
    @property
    def value(self) -> Any:
        """Renvoie la valeur de la condition"""
        return self._value
    
    @value.setter
    def value(self, value: Any) -> None:
        """Modifie la valeur de la condition"""
        self._value = value
        self.save()
    
    def save(self) -> None:
        """Sauvegarde la valeur de la condition"""
        self.__cog.data.execute(self.guild, "UPDATE conditions SET value = ? WHERE id = ?", (self._serialized, self.id))
        
    def delete(self) -> None:
        """Supprime la condition"""
        self.__cog.data.execute(self.guild, "DELETE FROM conditions WHERE id = ?", (self.id,))
        
    def check(self, func: Callable[[Any], bool]) -> bool:
        """Vérifie si la condition est remplie avec une fonction de test"""
        return func(self._value)
    
# COG =========================================================================
class Economy(commands.Cog):
    """Module central de gestion de l'économie"""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data = dataio.get_cog_data(self)
        
        ctx_account_menu = app_commands.ContextMenu(
            name='Compte bancaire',
            callback=self.ctx_account_info
        )
        self.bot.tree.add_command(ctx_account_menu)
        
        self.last_cleanup : float = 0
        
    def _init_guilds_db(self, guild: discord.Guild | None = None):
        guilds = [guild] if guild else self.bot.guilds
        accounts = """CREATE TABLE IF NOT EXISTS accounts (
            user_id INTEGER PRIMARY KEY,
            balance INTEGER CHECK (balance >= 0)
            )"""
        transactions = """CREATE TABLE IF NOT EXISTS transactions (
            id TEXT PRIMARY KEY,
            timestamp REAL,
            amount INTEGER,
            reason TEXT,
            user_id INTEGER,
            FOREIGN KEY (user_id) REFERENCES accounts (user_id)
            )"""
        config = """CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT
            )"""
        conditions = """CREATE TABLE IF NOT EXISTS conditions (
            id TEXT PRIMARY KEY,
            value TEXT
            )"""
        for g in guilds:
            self.data.execute(g, accounts, commit=False)
            self.data.execute(g, transactions, commit=False)
            self.data.execute(g, config, commit=False)
            self.data.execute(g, conditions, commit=False)
            self.data.commit(g)
            
            self.data.executemany(g, """INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)""", DEFAULT_CONFIG.items())
        
    @commands.Cog.listener()
    async def on_ready(self):
        self._init_guilds_db()
        
    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        self._init_guilds_db(guild)
    
    def cog_unload(self):
        self.data.close_all_databases()
            
    # Settings -----------------------------------------------------------------
    
    def get_guild_config(self, guild: discord.Guild) -> Dict[str, str]:
        """Renvoie la valeur d'un paramètre de configuration ou tous les paramètres de configuration"""
        r = self.data.fetchall(guild, "SELECT * FROM config")
        if r:
            return {row['key']: row['value'] for row in r}
        else:
            return DEFAULT_CONFIG
    
    def set_guild_config(self, guild: discord.Guild, key: str, value: Any) -> None:
        """Modifie la valeur d'un paramètre de configuration"""
        self.data.execute(guild, "INSERT OR REPLACE INTO config VALUES (?, ?)", (key, value))
    
    def get_currency(self, guild: discord.Guild) -> str:
        """Renvoie le symbole de la monnaie"""
        return str(self.get_guild_config(guild)['Currency'])
    
    # Accounts -----------------------------------------------------------------
    
    def get_account(self, user: discord.Member) -> Account:
        """Renvoie le compte d'un utilisateur"""
        return Account(self, user)
    
    def get_accounts(self, guild: discord.Guild) -> List[Account]:
        """Renvoie les comptes de tous les utilisateurs"""
        members = guild.members
        r = self.data.fetchall(guild, "SELECT * FROM accounts")
        return [Account(self, m) for m in members if m.id in [row['user_id'] for row in r]]
    
    # Stats & Utils --------------------------------------------------------------------
    
    #guild
    def get_guild_average_balance(self, guild: discord.Guild) -> int:
        """Renvoie la moyenne des soldes des comptes"""
        accounts = self.get_accounts(guild)
        return sum([a.balance for a in accounts]) // len(accounts)
    
    def get_guild_total_balance(self, guild: discord.Guild) -> int:
        """Renvoie la somme des soldes des comptes"""
        accounts = self.get_accounts(guild)
        return sum([a.balance for a in accounts])
    
    def get_guild_median_balance(self, guild: discord.Guild) -> int:
        """Renvoie la médiane des soldes des comptes"""
        accounts = self.get_accounts_by_balance(guild)
        return accounts[len(accounts) // 2].balance
    
    # accounts
    def get_accounts_by_balance(self, guild: discord.Guild, *, reverse: bool = True) -> List[Account]:
        """Renvoie les comptes de tous les utilisateurs triés par solde"""
        accounts = self.get_accounts(guild)
        return sorted(accounts, key=lambda a: a.balance, reverse=reverse)
    
    def get_account_rank(self, account: Account) -> int:
        """Renvoie le rang d'un compte"""
        accounts = self.get_accounts_by_balance(account.guild)
        return accounts.index(account) + 1
    
    # transactions
    def get_last_transactions(self, guild: discord.Guild, *, limit: int | None = 10) -> List[Transaction]:
        """Renvoie les dernières transactions de tous les utilisateurs"""
        if limit:
            rows = self.data.fetchall(guild, "SELECT * FROM transactions ORDER BY timestamp DESC LIMIT ?", (limit,))
        else:
            rows = self.data.fetchall(guild, "SELECT * FROM transactions ORDER BY timestamp DESC")
        members = {m.id: m for m in guild.members}
        return [Transaction(self, members[row['user_id']], row['amount'], reason=row['reason'], timestamp=row['timestamp']) for row in rows if row['user_id'] in members]
    
    def get_transactions_by_amount(self, guild: discord.Guild, *, reverse: bool = True) -> List[Transaction]:
        """Renvoie les transactions de tous les utilisateurs triées par montant"""
        transactions = self.get_last_transactions(guild, limit=None)
        return sorted(transactions, key=lambda t: t.amount, reverse=reverse)
    
    def get_transactions_since(self, guild: discord.Guild, since: datetime | float) -> List[Transaction]:
        """Renvoie les transactions de tous les utilisateurs depuis une date"""
        if isinstance(since, datetime):
            since = since.timestamp()
        rows = self.data.fetchall(guild, "SELECT * FROM transactions WHERE timestamp > ? ORDER BY timestamp DESC", (since,))
        members = {m.id: m for m in guild.members}
        return [Transaction(self, members[row['user_id']], row['amount'], reason=row['reason'], timestamp=row['timestamp']) for row in rows if row['user_id'] in members]
    
    # Data Management ----------------------------------------------------------
    
    def cleanup_transactions(self, guild: discord.Guild) -> None:
        """Supprime les transactions expirées"""
        self.data.execute(guild, "DELETE FROM transactions WHERE timestamp < ?", (datetime.now().timestamp() - TRANSACTION_EXPIRATION_DELAY,))
    
    # COMMANDES =================================================================
    
    # Commandes utilisateur ----------------------------------------------------
    
    bank_commands = app_commands.Group(name='bank', description="Gestion de votre compte bancaire", guild_only=True)
    
    @bank_commands.command(name='account')
    @app_commands.rename(user='utilisateur')
    async def _bank_account(self, interaction: discord.Interaction, user: discord.Member | None = None):
        """Affiche les informations sur votre compte bancaire
        
        :param user: Autre utilisateur dont on veut afficher les informations
        """
        member = user or interaction.user
        if not isinstance(member, discord.Member):
            return await interaction.response.send_message('**Erreur** · Vous devez mentionner un membre actuellement présent sur le serveur', ephemeral=True)
        
        account = self.get_account(member)
        await interaction.response.send_message(embed=account.embed)
        
    async def ctx_account_info(self, interaction: discord.Interaction, member: discord.Member):
        """Menu contextuel permettant l'affichage du compte bancaire virtuel d'un membre

        :param member: Utilisateur visé par la commande
        """
        account = self.get_account(member)
        await interaction.response.send_message(embed=account.embed, ephemeral=True)
        
    @bank_commands.command(name='history')
    @app_commands.rename(user='utilisateur')
    async def _bank_history(self, interaction: discord.Interaction, user: discord.Member | None = None):
        """Affiche l'historique de vos transactions

        :param user: Autre utilisateur dont on veut afficher l'historique
        """
        member = user or interaction.user
        if not isinstance(member, discord.Member):
            return await interaction.response.send_message('**Erreur** · Vous devez mentionner un membre actuellement présent sur le serveur', ephemeral=True)
        
        account = self.get_account(member)
        view = TransactionsHistoryView(interaction, account)
        await view.start()

    @bank_commands.command(name='give')
    @app_commands.rename(user='utilisateur', amount='montant', reason='raison')
    async def _bank_give(self, interaction: discord.Interaction, user: discord.Member, amount: app_commands.Range[int, 1], reason: str = ''):
        """Donner de l'argent à un utilisateur

        :param user: Utilisateur à qui donner de l'argent
        :param amount: Montant à donner
        :param reason: Raison du don (facultatif)
        """
        if amount < 0:
            return await interaction.response.send_message('**Erreur** · Le montant ne peut pas être négatif', ephemeral=True)
        
        if not isinstance(user, discord.Member):
            return await interaction.response.send_message('**Erreur** · Vous devez mentionner un membre actuellement présent sur le serveur', ephemeral=True)
        if not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message('**Erreur** · Vous devez être présent sur le serveur', ephemeral=True)
    
        giver = self.get_account(interaction.user)
        receiver = self.get_account(user)
        if giver == receiver:
            return await interaction.response.send_message('**Erreur** · Vous ne pouvez pas vous donner de l\'argent à vous-même', ephemeral=True)
        if giver.balance < amount:
            return await interaction.response.send_message('**Erreur** · Vous n\'avez pas assez d\'argent', ephemeral=True)
        
        giver.withdraw(amount, reason=f'Don à {user.display_name} > {reason}' if reason else f'Don à {user.display_name}')
        trs = receiver.deposit(amount, reason=f'Don de {interaction.user.display_name} > {reason}' if reason else f'Don de {interaction.user.display_name}')

        await interaction.response.send_message(f'**Transfert effectué** · **{trs.display_amount}** ont été envoyés à {user.mention} : `{reason}`' if reason else f'**Transfert effectué** · **{trs.display_amount}** ont été envoyés à {user.mention}')
    
    # Commandes globales -------------------------------------------------------
    
    @app_commands.command(name='daily')
    @app_commands.guild_only()
    async def _bank_daily(self, interaction: discord.Interaction):
        """Récupérer votre aide économique quotidienne"""
        user = interaction.user
        if not isinstance(user, discord.Member):
            return await interaction.response.send_message('**Erreur** · Vous devez être présent sur le serveur', ephemeral=True)
        
        account = self.get_account(user)
        today = datetime.now().strftime('%d.%m.%Y')
        config = self.get_guild_config(user.guild)
        
        dailyamount = int(config['DailyAmount'])
        dailylimit = int(config['DailyLimit'])
        if dailyamount <= 0 or dailylimit <= 0:
            return await interaction.response.send_message("**Erreur** · L'aide économique quotidienne n'est pas disponible sur ce serveur", ephemeral=True)
        
        ignore_part = 0.1 * dailylimit # On ignore l'équivallent de 10% de la limite quotidienne dans le calcul de la réduction
        if account.balance > ignore_part:
            redux = (account.balance - ignore_part) / dailylimit
            dailyamount = round(dailyamount * (1 - redux))
        
        cond = Condition(self, 'LastDaily', user, default_value='')
        if cond.check(lambda v: v == today):
            return await interaction.response.send_message("**Erreur** · Vous avez déjà récupéré votre aide quotidienne aujourd'hui, réessayez demain.", ephemeral=True)
        
        if account.balance >= dailylimit:
            return await interaction.response.send_message(f"**Erreur** · Vous avez déjà atteint la limite maximale donnant droit à l'aide quotidienne ({config['DailyLimit']}{config['Currency']})", ephemeral=True)
        
        if dailyamount <= 0:
            return await interaction.response.send_message("**Solde trop élevé** · L'aide qu'il vous reste à percevoir est inférieure à un crédit", ephemeral=True)
        
        trs = account.deposit(dailyamount, reason=f'Aide quotidienne du {today}')
        cond.value = today
        await interaction.response.send_message(f"**Aide quotidienne récupérée** · **{trs.display_amount}** ont été ajoutés à votre compte au titre de l'aide économique quotidienne")
    
    @app_commands.command(name='leaderboard')
    @app_commands.guild_only()
    async def _leaderboard(self, interaction: discord.Interaction):
        """Affiche un top 20 des comptes bancaires du serveur"""
        guild = interaction.guild
        if not isinstance(guild, discord.Guild):
            return await interaction.response.send_message('**Erreur** · Vous devez être présent sur le serveur', ephemeral=True)
        
        accounts = self.get_accounts_by_balance(guild)
        if not accounts:
            return await interaction.response.send_message("**Erreur** · Aucun compte bancaire n'a été ouvert sur ce serveur")
        
        user = interaction.user
        if not isinstance(user, discord.Member):
            return await interaction.response.send_message('**Erreur** · Vous devez être présent sur le serveur', ephemeral=True)
        
        top = accounts[:20]
        txt = '\n'.join([f'{i+1}. {a.owner.mention} · **{a.display_balance}**' for i, a in enumerate(top)])
        em = discord.Embed(title=f"Leaderboard · ***{guild.name}***", description=txt, color=0x2b2d31)
        if user not in [a.owner for a in top]:
            em.add_field(name="Votre rang", value=pretty.codeblock(f'#{self.get_account_rank(self.get_account(user))}'))
        em.set_footer(text=f"Total sur le serveur · {self.get_guild_total_balance(guild)}{self.get_currency(guild)}")
        await interaction.response.send_message(embed=em)
        
    @app_commands.command(name='stats')
    @app_commands.guild_only()
    async def _stats(self, interaction: discord.Interaction):
        """Affiche diverses statistiques sur l'économie du serveur"""
        guild = interaction.guild
        if not isinstance(guild, discord.Guild):
            return await interaction.response.send_message('**Erreur** · Vous devez être présent sur le serveur', ephemeral=True)
        
        global_variation = self.get_transactions_since(guild, datetime.now() - timedelta(hours=24))
        global_variation = sum([t.amount for t in global_variation])
        currency = self.get_currency(guild)
        
        em = discord.Embed(title=f"Statistiques de l'économie · ***{guild.name}***", color=0x2b2d31)
        richest = self.get_accounts_by_balance(guild)[0]
        em.add_field(name="Plus riche", value=pretty.codeblock(f"{richest.owner.name} · {richest.balance}{currency}"))
        em.add_field(name="Moyenne", value=pretty.codeblock(str(self.get_guild_average_balance(guild)) + f'{currency}'))
        em.add_field(name="Médiane", value=pretty.codeblock(str(self.get_guild_median_balance(guild)) + f'{currency}'))
        em.add_field(name="Variation s/ 24h", value=pretty.codeblock(f'{global_variation:+}', lang='diff'))
        em.add_field(name="Total en circulation", value=pretty.codeblock(str(self.get_guild_total_balance(guild)) + f'{currency}'))
        await interaction.response.send_message(embed=em)
    
    # Commandes d'administration -----------------------------------------------    
    
    config_commands = app_commands.Group(name='configbank', description="Paramètres de la banque du serveur", guild_only=True, default_permissions=discord.Permissions(manage_guild=True))
    
    @config_commands.command(name='reset')
    @app_commands.rename(user='utilisateur')
    async def _configbank_reset(self, interaction: discord.Interaction, user: discord.Member):
        """Réinitialise le solde d'un utilisateur

        :param user: Utilisateur dont on veut réinitialiser le solde
        """
        if not isinstance(user, discord.Member):
            return await interaction.response.send_message('**Erreur** · Vous devez mentionner un membre actuellement présent sur le serveur', ephemeral=True)
        
        account = self.get_account(user)
        account.reset()
        default_balance = int(self.get_guild_config(user.guild)['DefaultBalance'])
        await interaction.response.send_message(f"**Solde réinitialisé** · Le solde de {user.mention} a été réinitialisé à **{default_balance}{self.get_currency(user.guild)}**")
        
    @config_commands.command(name='setbalance')
    @app_commands.rename(user='utilisateur', amount='montant')
    async def _configbank_setbalance(self, interaction: discord.Interaction, user: discord.Member, amount: app_commands.Range[int, 0]):
        """Modifie le solde d'un utilisateur

        :param user: Utilisateur dont on veut modifier le solde
        :param amount: Nouveau solde à attribuer
        """
        if not isinstance(user, discord.Member):
            return await interaction.response.send_message('**Erreur** · Vous devez mentionner un membre actuellement présent sur le serveur', ephemeral=True)
        
        account = self.get_account(user)
        account.set(amount, reason=f"Modification du solde par {interaction.user.display_name}")
        await interaction.response.send_message(f"**Solde modifié** · Le solde de {user.mention} a été modifié à **{amount}{self.get_currency(user.guild)}**")
        
    @config_commands.command(name='cancel')
    async def _configbank_cancel(self, interaction: discord.Interaction, transaction_id: str):
        """Annule une transaction d'un utilisateur

        :param transaction_id: Identifiant de la transaction
        """
        if not isinstance(interaction.guild, discord.Guild):
            return await interaction.response.send_message('**Erreur** · Vous devez être présent sur le serveur', ephemeral=True)
        
        trs = Transaction.from_id(self, interaction.guild, transaction_id)
        if not trs:
            return await interaction.response.send_message('**Erreur** · Cette transaction n\'existe pas', ephemeral=True)
        
        await interaction.response.defer()
        view = ConfirmationView()
        await interaction.followup.send('Êtes-vous sûr de vouloir annuler cette transaction ?', view=view, embed=trs.embed)
        await view.wait()
        if not view.value:
            return await interaction.edit_original_response(content='**Annulation** · La transaction n\'a pas été annulée', view=None, embed=None)
                
        account = self.get_account(trs.user)
        account.cancel(trs)
        await interaction.edit_original_response(content=f"**Transaction annulée** · La transaction `{trs.id}` a été annulée et le solde de {trs.user.mention} a été modifié à **{account.display_balance}**", view=None, embed=None)
                
    @_configbank_cancel.autocomplete('transaction_id')
    async def transaction_id_autocomplete(self, interaction: discord.Interaction, current: str):
        if not isinstance(interaction.guild, discord.Guild):
            return []
        last_trs = self.get_last_transactions(interaction.guild, limit=20)
        r = fuzzy.finder(current, last_trs, key=lambda t: t.id)
        choices = [app_commands.Choice(name=f'{trs.frelative} > {trs.user.name} {trs.amount:+}', value=trs.id) for trs in r]
        return sorted(choices, key=lambda c: c.name)
    
    @config_commands.command(name='currency')
    @app_commands.rename(currency='symbole')
    async def _configbank_currency(self, interaction: discord.Interaction, currency: str):
        """Modifie le symbole de la monnaie du serveur
        
        :param currency: Symbole de la monnaie
        """
        guild = interaction.guild
        if not isinstance(guild, discord.Guild):
            return await interaction.response.send_message('**Erreur** · Vous devez être présent sur le serveur', ephemeral=True)
        
        # On vérifie que le symbole soit valide (unicode)
        if not currency.isprintable() or currency.isspace():
            return await interaction.response.send_message('**Erreur** · Le symbole de la monnaie doit être un caractère unicode non-vide et imprimable', ephemeral=True)
        
        if len(currency) > 3:
            return await interaction.response.send_message('**Erreur** · Le symbole de la monnaie ne peut pas dépasser 3 caractères', ephemeral=True)
        
        self.set_guild_config(guild, 'Currency', currency)
        await interaction.response.send_message(f"**Paramètre modifié** · Le symbole de la monnaie a été modifié pour `{currency}`")
        
    @config_commands.command(name='daily')
    @app_commands.rename(amount='montant', limit='limite')
    async def _configbank_daily(self, interaction: discord.Interaction, amount: app_commands.Range[int, 0], limit: app_commands.Range[int, 0]):
        """Modifie les paramètres de l'aide économique quotidienne (0 pour désactiver)

        :param amount: Montant de l'aide quotidienne
        :param limit: Limite de solde pour recevoir l'aide quotidienne
        """
        guild = interaction.guild
        if not isinstance(guild, discord.Guild):
            return await interaction.response.send_message('**Erreur** · Vous devez être présent sur le serveur', ephemeral=True)
        
        self.set_guild_config(guild, 'DailyAmount', amount)
        self.set_guild_config(guild, 'DailyLimit', limit)
        if amount == 0 or limit == 0:
            await interaction.response.send_message(f"**Paramètres modifiés** · L'aide économique quotidienne a été désactivée")
        else:
            await interaction.response.send_message(f"**Paramètres modifiés** · L'aide économique quotidienne a été modifiée pour `{amount}{self.get_currency(guild)}` avec une limite de `{limit}{self.get_currency(guild)}`")
    
    @config_commands.command(name='defaultbalance')
    @app_commands.rename(amount='montant')
    async def _configbank_defaultbalance(self, interaction: discord.Interaction, amount: app_commands.Range[int, 0]):
        """Modifie le solde par défaut des comptes bancaires lors de leur création

        :param amount: Solde par défaut
        """
        guild = interaction.guild
        if not isinstance(guild, discord.Guild):
            return await interaction.response.send_message('**Erreur** · Vous devez être présent sur le serveur', ephemeral=True)
        
        self.set_guild_config(guild, 'DefaultBalance', amount)
        await interaction.response.send_message(f"**Paramètre modifié** · Le solde par défaut a été modifié pour `{amount}{self.get_currency(guild)}`")
    
async def setup(bot):
    cog = Economy(bot)
    await bot.add_cog(cog)