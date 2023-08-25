import logging
import random
import re
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands
from tabulate import tabulate

from common import dataio
from common.utils import pretty

logger = logging.getLogger(f'Analog.{__name__.capitalize()}')

# CLASSES =====================================================================

class Dice:
    """Représente un dé à plusieurs faces."""
    def __init__(self, faces: list[int]):
        self.faces = faces
        
    def __repr__(self):
        return f'<Dice faces={self.faces}>'
    
    def __str__(self):
        return f"d({','.join([str(f) for f in self.faces])})"
    
    def __eq__(self, __value: object) -> bool:
        return isinstance(__value, Dice) and self.faces == __value.faces
    
    def __hash__(self) -> int:
        return hash(tuple(self.faces))
    
    # SERIALIZATION -----------------------------------------------------------
    
    def _to_string(self) -> str:
        return '/'.join([str(f) for f in self.faces])
    
    @classmethod
    def _from_string(cls, string: str):
        return cls([int(f) for f in string.split('/')])
    
    # ROLLING -----------------------------------------------------------------
    
    def roll(self) -> int:
        """Lance le dé."""
        return random.choice(self.faces)
    
class ClassicDice(Dice):
    """Représente un dé classique à N faces."""
    def __init__(self, faces: int):
        super().__init__(list(range(1, faces + 1)))
        
    def __repr__(self):
        return f'<ClassicDice faces={len(self.faces)}>'
    
    def __str__(self):
        return f"d{len(self.faces)}"
    
class DiceThrow:
    """Représente un jet de plusieurs dés."""
    def __init__(self, dices: list[Dice]):
        self.dices = dices
        
    def __repr__(self):
        return f'<DiceThrow dices={self.dices}>'
    
    def __str__(self):
        # On compte les dés identiques et on les affiche sous la forme NdF
        dices = {}
        for d in self.dices:
            if d in dices:
                dices[d] += 1
            else:
                dices[d] = 1
                
        return ' + '.join([f'{dices[d]}{str(d)}' for d in dices])
    
    # SERIALIZATION -----------------------------------------------------------
    
    def _to_string(self) -> str:
        return ','.join([d._to_string() for d in self.dices])
    
    @classmethod
    def _from_string(cls, string: str):
        return cls([Dice._from_string(d) for d in string.split(',')])
    
    # ROLLING -----------------------------------------------------------------
    
    def roll_sum(self) -> int:
        return sum([d.roll() for d in self.dices])
    
    def roll_all(self) -> list[int]:
        return [d.roll() for d in self.dices]
    
class ThrowTransformer(app_commands.Transformer):
    """Convertit une chaîne en un jet de dés."""
    
    async def transform(self, interaction: discord.Interaction, value: str) -> Any:
        dices = []
        string = [v.strip() for v in value.split('+')]
        for dice in string:
            # Les dés classiques sont sous la forme NdF 
            if re.match(r'^\d+d\d+$', dice):
                n, f = dice.split('d')
                for _ in range(int(n) or 1):
                    dices.append(ClassicDice(int(f)))
            # Les dés personnalisés sont sous la forme Nd(F1,F2,FN...)
            elif re.match(r'^\d+d\(\d+(,\d+)*\)$', dice):
                n, f = dice.split('d')
                faces = [int(f.strip()) for f in f[1:-1].split(',')]
                for _ in range(int(n) or 1):
                    dices.append(Dice(faces))
            else:
                await interaction.response.send_message(f"**Invalide** · Les dés doivent être au format NdF ou Nd(F1,F2,FN...) et séparés par des '+'")
                raise commands.BadArgument('Format de lancer de dés invalide')
        
        if len(dices) > 20:
            await interaction.response.send_message(f"**Trop de dés** · Vous ne pouvez pas lancer plus de 20 dés à la fois")
            raise commands.BadArgument('Nombre de dés trop important')
            
        return DiceThrow(dices)
        
class GameTools(commands.Cog):
    """Divers outils de jeu pour vos soirées endiablées."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data = dataio.get_cog_data(self)
        
        self.throws : dict[int, DiceThrow] = {}
        
    def _init_guilds_db(self, guild: discord.Guild | None = None):
        guilds = [guild] if guild else list(self.bot.guilds)
        for g in guilds:
            throws = """CREATE TABLE IF NOT EXISTS throws (
                name TEXT PRIMARY KEY,
                throw TEXT
                )"""
            self.data.execute(g, throws)
        
    @commands.Cog.listener()
    async def on_ready(self):
        self._init_guilds_db()
        
    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        self._init_guilds_db(guild)
    
    def cog_unload(self):
        self.data.close_all_databases()
        
    # THROW SAVING SYSTEM ------------------------------------------------------
    
    def load_throw(self, guild: discord.Guild, name: str) -> dict | None:
        """Charge un jet de dés sauvegardé."""
        query = """SELECT * FROM throws WHERE name = ?"""
        data = self.data.fetchone(guild, query, (name,))
        if data:
            return {
                'name': data[0],
                'throw': DiceThrow._from_string(data[1])
            }
        return None
    
    def save_throw(self, guild: discord.Guild, name: str, throw: DiceThrow):
        """Sauvegarde un jet de dés"""
        query = """INSERT OR REPLACE INTO throws VALUES (?, ?)"""
        self.data.execute(guild, query, (name, throw._to_string()))
        
    def delete_throw(self, guild: discord.Guild, name: str):
        """Efface un jet de dés sauvegardé."""
        query = """DELETE FROM throws WHERE name = ?"""
        self.data.execute(guild, query, (name,))
        
    def get_throws(self, guild: discord.Guild) -> list[dict]:
        """Liste les jets de dés sauvegardés."""
        query = """SELECT * FROM throws"""
        data = self.data.fetchall(guild, query)
        return [{
            'name': d[0],
            'dices': DiceThrow._from_string(d[1])
        } for d in data]
        
    # COMMANDS =================================================================
    
    @app_commands.command(name='flip')
    async def flip_coin(self, interaction: discord.Interaction):
        """Lancer une pièce"""
        result = random.choice(('Pile', 'Face'))
        await interaction.response.send_message(f"`🪙` **{result}** !")
    
    @app_commands.command(name='roll')
    @app_commands.rename(dices='dés')
    async def dice_roll(self, interaction: discord.Interaction, dices: app_commands.Transform[DiceThrow, ThrowTransformer]):
        """Réaliser un lancer de dés

        :param dices: Dés à lancer (format NdF ou Nd(F1,F2,FN...))
        """
        rolls = []
        for d in dices.dices:
            rolls.append((str(d), d.roll()))
        text = pretty.codeblock(tabulate(rolls, tablefmt='plain'))
        em = discord.Embed(description=f"# `🎲 {dices}`\n{text}")
        await interaction.response.send_message(embed=em)
        self.throws[interaction.user.id] = dices
        
    # TODO: Sauvegarde des dés
    
async def setup(bot):
    cog = GameTools(bot)
    await bot.add_cog(cog)
