import logging
import re
from typing import Dict, Optional

import discord
from discord import app_commands
from discord.ext import commands
from tabulate import tabulate

from cogs.economy.economy import Economy
from common import dataio
from common.utils import fuzzy, pretty

logger = logging.getLogger(f'Analog.{__name__.capitalize()}')    

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
        
class Gambling(commands.Cog):
    """Créez et gérez des paris sur divers événements."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data = dataio.get_cog_data(self)
        
    def _init_guilds_db(self, guild: discord.Guild | None = None):
        guilds = [guild] if guild else list(self.bot.guilds)
        for g in guilds:
            bettings = """CREATE TABLE IF NOT EXISTS bettings (
                channel_id INTEGER PRIMARY KEY,
                title TEXT,
                choices TEXT,
                message_id INTEGER DEFAULT 0,
                minimal_bet INTEGER DEFAULT 0,
                author_id INTEGER
                )"""
            self.data.execute(g, bettings)
            
            bets = """CREATE TABLE IF NOT EXISTS bets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                channel_id INTEGER,
                choice TEXT,
                amount INTEGER,
                FOREIGN KEY (channel_id) REFERENCES bettings (channel_id)
                )"""
            self.data.execute(g, bets)
        
    @commands.Cog.listener()
    async def on_ready(self):
        self._init_guilds_db()
        
        await self.bot.wait_until_ready()
        self.economy : Economy = self.bot.get_cog('Economy') # type: ignore
        if not self.economy:
            logger.warning(f"Impossible de charger le cog 'Economy'")
            return await self.bot.unload_extension(self.__cog_name__)
        
    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        self._init_guilds_db(guild)
    
    def cog_unload(self):
        self.data.close_all_databases()
        
    # BETTINGS ---------------------------------------------------------------
    
    def get_betting(self, channel: discord.TextChannel | discord.Thread) -> Optional[Dict]:
        """Récupère les informations d'un pari."""
        query = """SELECT * FROM bettings WHERE channel_id = ?"""
        r = self.data.fetchone(channel.guild, query, (channel.id,))
        if r:
            return dict(r)
        return None
    
    def get_all_bettings(self, guild: discord.Guild) -> list[Dict]:
        """Récupère les informations de tous les paris d'un serveur."""
        query = """SELECT * FROM bettings"""
        r = self.data.fetchall(guild, query)
        if r:
            return [dict(b) for b in r]
        return []
    
    def set_betting(self, channel: discord.TextChannel | discord.Thread, title: str, choices: list[str], message: discord.Message, minimal_bet: int, author: discord.User | discord.Member):
        """Crée ou met à jour un pari."""
        query = """INSERT OR REPLACE INTO bettings VALUES (?, ?, ?, ?, ?, ?)"""
        self.data.execute(channel.guild, query, (channel.id, title, ','.join([c.lower() for c in choices]), message.id, minimal_bet, author.id))
    
    def delete_betting(self, channel: discord.TextChannel | discord.Thread):
        """Supprime un pari."""
        query = """DELETE FROM bettings WHERE channel_id = ?"""
        self.data.execute(channel.guild, query, (channel.id,))
    
    # BETS -------------------------------------------------------------------
    
    def get_bets(self, channel: discord.TextChannel | discord.Thread) -> list[Dict]:
        """Récupère les paris d'un pari."""
        query = """SELECT * FROM bets WHERE channel_id = ?"""
        r = self.data.fetchall(channel.guild, query, (channel.id,))
        if r:
            return [dict(b) for b in r]
        return []
    
    def get_bet(self, channel: discord.TextChannel | discord.Thread, user: discord.User | discord.Member) -> Optional[Dict]:
        """Récupère le pari d'un utilisateur."""
        query = """SELECT * FROM bets WHERE channel_id = ? AND user_id = ?"""
        r = self.data.fetchone(channel.guild, query, (channel.id, user.id))
        if r:
            return dict(r)
        return None
    
    def set_bet(self, channel: discord.TextChannel | discord.Thread, user: discord.User | discord.Member, choice: str, amount: int):
        """Crée ou met à jour un pari."""
        # On vérifie que le pari existe
        if not self.get_betting(channel):
            raise ValueError(f"Le pari n'existe pas.")
        
        choice = choice.lower()
        
        # On update s'il ajoute de l'argent
        current_bet = self.get_bet(channel, user)
        if current_bet: # Il a déjà parié
            query = """UPDATE bets SET choice = ?, amount = ? WHERE channel_id = ? AND user_id = ?"""
            self.data.execute(channel.guild, query, (choice, amount, channel.id, user.id))
            return
        
        query = """INSERT INTO bets VALUES (?, ?, ?, ?, ?)"""
        self.data.execute(channel.guild, query, (None, user.id, channel.id, choice, amount))
        
    def delete_bet(self, channel: discord.TextChannel | discord.Thread, user: discord.User | discord.Member):
        """Supprime un pari."""
        query = """DELETE FROM bets WHERE channel_id = ? AND user_id = ?"""
        self.data.execute(channel.guild, query, (channel.id, user.id))
        
    def delete_all_bets(self, channel: discord.TextChannel | discord.Thread):
        """Supprime tous les paris d'un pari."""
        query = """DELETE FROM bets WHERE channel_id = ?"""
        self.data.execute(channel.guild, query, (channel.id,))
        
    async def handle_winners(self, channel: discord.TextChannel | discord.Thread, result: str):
        """Attribue les gains aux gagnants d'un pari et les notifie."""
        data = self.get_betting(channel)
        if not data:
            raise ValueError(f"Le pari n'existe pas.")
        
        # On récupère les paris
        bets = self.get_bets(channel)
        if not bets:
            return
        
        bet_channel = channel.guild.get_channel(data['channel_id'])
        if not bet_channel or not isinstance(bet_channel, (discord.TextChannel, discord.Thread)):
            return await channel.send(f"**Salon invalide** · Le salon du pari n'existe plus ou n'est plus accessible.")
        
        # On récupère le choix gagnant
        winner = result.lower()
        if winner not in data['choices'].split(','):
            return await channel.send(f"**Choix invalide** · Le choix `{winner}` n'existe pas.")
        
        # On calcule le total des mises
        total = sum([b['amount'] for b in bets])
        if not total:
            return await channel.send(f"**Aucun pari** · Personne n'a parié sur ce pari.")
        
        # On récupère les gagnants
        winners = [b for b in bets if b['choice'] == winner]
        if not winners:
            embed = self.get_betting_embed(channel, highlight_result=winner)
            embed.set_author(name="Pari terminé · Résultats")
            return await channel.send(f"# Pari terminé · `{data['title']}`\nPersonne n'a parié sur `{winner.capitalize()}` ! Il n'y a donc pas de gagnant.\n### Résultats", embed=embed)

        embed = self.get_betting_embed(channel, highlight_result=winner)
        embed.set_author(name="Pari terminé · Résultats")
        
        # On distribue les gains
        table = []
        mentions = []
        currency = self.economy.get_currency(channel.guild)
        winners = sorted(winners, key=lambda w: w['amount'], reverse=True)
        for winner in winners:
            member = channel.guild.get_member(winner['user_id'])
            if not member:
                table.append((winner['user_id'], winner['amount']))
            else:
                amount = int(winner['amount'] * total / winner['amount'])
                table.append((str(member), f'+{amount}{currency}'))
                account = self.economy.get_account(member)
                account.deposit(amount, reason=f"Gains du pari {data['title']}")
                mentions.append(member.mention)
        
        embed.add_field(name="Gagnants" if len(winners) > 1 else "Gagnant", value=pretty.codeblock(tabulate(table, headers=('Membre', 'Gains'))), inline=False)
        # On notifie les gagnants
        await bet_channel.send(f"# **Pari terminé** · `{data['title']}`\n{' '.join(mentions)}\n### Résultats", embed=embed)
       
    # DISPLAY ----------------------------------------------------------------
    
    def get_betting_embed(self, channel: discord.TextChannel | discord.Thread, *, highlight_result: str | None = None) -> discord.Embed:
        """Renvoie un embed avec les informations du pari mis à jour."""
        data = self.get_betting(channel)
        if not data:
            raise ValueError(f"Le pari n'existe pas.")
        
        currency = self.economy.get_currency(channel.guild)
        embed = discord.Embed(title=data['title'], color=0x2b2d31)
        embed.set_author(name="Pari en cours" if not highlight_result else "Pari terminé")
        bets = self.get_bets(channel)
        table = []
        choices = data['choices'].split(',')
        if not bets:
            for choice in choices:
                if highlight_result:
                    if highlight_result == choice:
                        table.append(('+' + choice.capitalize(), f'0{currency}', ''))
                    else:
                        table.append(('-' + choice.capitalize(), f'0{currency}', ''))
                else:
                    table.append((choice.capitalize(), 0, ''))
        else:
            total = sum([b['amount'] for b in bets])
            for choice in choices:
                amount = sum([b['amount'] for b in bets if b['choice'] == choice])
                prc = amount / total * 100 if total else 0
                bar = pretty.bar_chart(amount, total, 10) + f' {round(prc)}%'
                if highlight_result:
                    if highlight_result == choice:
                        table.append(('+' + choice.capitalize(), f'{amount}{currency}', bar))
                    else:
                        table.append(('-' + choice.capitalize(), f'{amount}{currency}', bar))
                else:
                    table.append((choice.capitalize(), f'{amount}{currency}', bar))
                
        embed.description = pretty.codeblock(tabulate(table, tablefmt='plain'), lang='diff')
        
        author = channel.guild.get_member(data['author_id'])
        if author:
            avatar = author.display_avatar.url
        elif channel.guild.icon:
            avatar = channel.guild.icon.url
        else:
            avatar = None
        
        if data['minimal_bet'] > 1:
            embed.set_footer(text=f"Pariez avec /bet · Minimum {data['minimal_bet']}{self.economy.get_currency(channel.guild)}", icon_url=avatar)
        else:
            embed.set_footer(text="Pariez avec /bet", icon_url=avatar)
        return embed
    
    async def update_display(self, channel: discord.TextChannel | discord.Thread, result: str | None = None):
        """Met à jour l'affichage du pari."""
        data = self.get_betting(channel)
        if not data:
            raise ValueError(f"Le pari n'existe pas.")
        
        if result:
            embed = self.get_betting_embed(channel, highlight_result=result)
        else:
            embed = self.get_betting_embed(channel)
        message = await channel.fetch_message(data['message_id'])
        await message.edit(embed=embed, content=None)
        
    # COMMANDES ===============================================================
    
    gamble_commands = app_commands.Group(name='gamble', description="Créer et gérer les paris", guild_only=True)
    
    @gamble_commands.command(name='start')
    @app_commands.rename(title='titre', choices='choix', minimal_bet='mise_minimale')
    async def _start_gamble(self, interaction: discord.Interaction, title: str, choices: str, minimal_bet: app_commands.Range[int, 1] = 1):
        """Lancer un nouveau pari sur le salon actuel

        :param title: Titre du pari (max. 100 caractères)
        :param choices: Choix possibles séparés par des virgules ou des barres verticales
        :param minimal_bet: Mise minimale pour participer au pari (facultatif)
        """
        channel = interaction.channel
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return await interaction.response.send_message("**Salon invalide** · Cette commande ne peut être utilisée que sur un salon textuel ou un fil de discussion.", ephemeral=True)
        
        if self.get_betting(channel):
            return await interaction.response.send_message(f"**Pari déjà en cours** · Un pari est déjà en cours sur ce salon.\nArrêtez le avec </gamble stop:0> pour annoncer le résultat.", ephemeral=True)
        
        if len(title) > 100:
            return await interaction.response.send_message("**Titre invalide** · Le titre ne peut pas dépasser 100 caractères.", ephemeral=True)
        
        chx = [c.strip().lower() for c in re.split(r'[,|;]', choices) if c]
        if len(chx) < 2 or len(chx) > 4:
            return await interaction.response.send_message("**Choix invalides** · Vous devez spécifier entre 2 et 4 choix possibles.", ephemeral=True)
        
        message = await channel.send("`⏳` **Chargement de l'affichage des résultats...**")
        self.set_betting(channel, title, chx, message, minimal_bet, interaction.user)
        
        await self.update_display(channel)
        
        alert = ""
        try:
            await message.pin()
        except discord.HTTPException:
            alert = "\n**Attention** · Je n'ai pas pu épingler le message du pari. Assurez-vous que j'ai la permission `Gérer les messages` sur ce salon et qu'il n'y a pas déjà 50 messages d'épinglés."
            
        await interaction.response.send_message(f"**Pari créé** · Le pari a été créé sur ce salon.\nVous pouvez parier avec `/bet`.{alert}", ephemeral=True)

    @gamble_commands.command(name='stop')
    @app_commands.rename(result='résultat')
    async def _stop_gamble(self, interaction: discord.Interaction, result: str):
        """Arrête le pari en cours sur le salon actuel
        
        :param result: Résultat du pari"""
        channel = interaction.channel
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return await interaction.response.send_message("**Salon invalide** · Cette commande ne peut être utilisée que sur un salon textuel ou un fil de discussion.", ephemeral=True)
        
        betting = self.get_betting(channel)
        if not betting:
            return await interaction.response.send_message(f"**Aucun pari en cours** · Aucun pari n'est en cours sur ce salon.", ephemeral=True)
        
        # Il faut que ce soit un modérateur ou l'auteur du pari
        if not channel.permissions_for(interaction.user).manage_messages: # type: ignore
            author = channel.guild.get_member(betting['author_id'])
            if not author or interaction.user != author:
                return await interaction.response.send_message(f"**Autorisation insuffisante** · Vous devez être modérateur ou l'auteur du pari pour l'arrêter.", ephemeral=True)
        
        await interaction.response.defer()
        # Message de confirmation
        view = ConfirmationView(custom_labels=('Terminer', 'Annuler'))
        await interaction.followup.send(f"**Terminer le pari** · Êtes-vous sûr de vouloir arrêter le pari en cours sur ce salon et annoncer le résultat ?", view=view, ephemeral=True)
        await view.wait()
        if view.value is None:
            return await interaction.edit_original_response(content="**Arrêt annulé** · Vous n'avez pas répondu à temps.", view=None)
        elif view.value is False:
            return await interaction.edit_original_response(content=f"**Arrêt annulé** · Le pari n'a pas été arrêté.", view=None)
        
        # On vérifie que le résultat est valide 
        if result.lower() not in betting['choices'].split(','):
            return await interaction.followup.send(f"**Résultat invalide** · Le résultat `{result}` n'existe pas.", ephemeral=True)
        
        alert = ""
        bet_message = await channel.fetch_message(betting['message_id'])
        if not bet_message:
            pass
        elif bet_message.pinned:
            try:
                await bet_message.unpin()
            except discord.HTTPException:
                alert = "\n**Attention** · Je n'ai pas pu désépingler le message du pari. Assurez-vous que j'ai la permission `Gérer les messages` sur ce salon."
        
        await self.handle_winners(channel, result)
        self.delete_betting(channel)
        self.delete_all_bets(channel)
        await interaction.delete_original_response()
        
        await interaction.followup.send(content=f"**Pari arrêté** · Le pari a été arrêté sur ce salon.{alert}", ephemeral=True)
        
    @gamble_commands.command(name='cancel')
    async def _cancel_gamble(self, interaction: discord.Interaction):
        """Annule le pari en cours sur le salon et rembourse les participants"""
        channel = interaction.channel
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return await interaction.response.send_message("**Salon invalide** · Cette commande ne peut être utilisée que sur un salon textuel ou un fil de discussion.", ephemeral=True)
        
        betting = self.get_betting(channel)
        if not betting:
            return await interaction.response.send_message(f"**Aucun pari en cours** · Aucun pari n'est en cours sur ce salon.", ephemeral=True)
        
        # Il faut que ce soit un modérateur ou l'auteur du pari
        if not channel.permissions_for(interaction.user).manage_messages: # type: ignore
            author = channel.guild.get_member(betting['author_id'])
            if not author or interaction.user != author:
                return await interaction.response.send_message(f"**Autorisation insuffisante** · Vous devez être modérateur ou l'auteur du pari pour l'annuler.", ephemeral=True)
        
        await interaction.response.defer()
        # Message de confirmation
        view = ConfirmationView(custom_labels=('Rembourser', 'Annuler'))
        await interaction.followup.send(f"**Annuler le pari** · Êtes-vous sûr de vouloir annuler le pari en cours sur ce salon et rembourser les participants ?", view=view, ephemeral=True)
        await view.wait()
        if view.value is None:
            return await interaction.edit_original_response(content="**Annulation annulée** · Vous n'avez pas répondu à temps.", view=None)
        elif view.value is False:
            return await interaction.edit_original_response(content=f"**Annulation annulée** · Le pari n'a pas été annulé.", view=None)
        
        # On rembourse les participants
        bets = self.get_bets(channel)
        if bets:
            for bet in bets:
                member = channel.guild.get_member(bet['user_id'])
                if not member:
                    continue
                account = self.economy.get_account(member)
                account.deposit(bet['amount'], reason=f"Remboursement du pari {betting['title']}")
        else:
            return await interaction.followup.send(f"**Aucun pari** · Personne n'a parié sur ce pari.", ephemeral=True)

        self.delete_betting(channel)
        self.delete_all_bets(channel)
        await interaction.delete_original_response()
        
        bet_message = await channel.fetch_message(betting['message_id'])
        if bet_message:
            await bet_message.delete()
        
        await interaction.followup.send(content=f"**Pari annulé** · Le pari `{betting['title']}` en cours ce salon a été annulé.\nTous les participants ont été remboursés.")
        
    @app_commands.command(name='bet')
    @app_commands.guild_only()
    @app_commands.rename(choice='choix', amount='montant')
    async def _bet_choice(self, interaction: discord.Interaction, choice: str, amount: app_commands.Range[int, 1]):
        """Pariez sur un choix d'un pari en cours

        :param choice: Choix sur lequel parier
        :param amount: Montant à parier
        """
        channel = interaction.channel
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return await interaction.response.send_message("**Salon invalide** · Cette commande ne peut être utilisée que sur un salon textuel ou un fil de discussion.", ephemeral=True)
        if not isinstance(interaction.user, (discord.Member)):
            return await interaction.response.send_message("**Utilisateur invalide** · Cette commande ne peut être utilisée que par un membre du serveur.", ephemeral=True)
        
        currency = self.economy.get_currency(channel.guild)
        data = self.get_betting(channel)
        if not data:
            return await interaction.response.send_message(f"**Aucun pari en cours** · Aucun pari n'est en cours sur ce salon.", ephemeral=True)
        
        if choice not in data['choices'].split(','):
            return await interaction.response.send_message(f"**Choix invalide** · Le choix `{choice}` n'existe pas.", ephemeral=True)
        
        if amount < data['minimal_bet']:
            return await interaction.response.send_message(f"**Mise trop faible** · La mise minimale est de {data['minimal_bet']}{currency}.", ephemeral=True)
        
        await interaction.response.defer(ephemeral=True)
        account = self.economy.get_account(interaction.user)
        if account.balance < amount:
            return await interaction.followup.send(f"**Solde insuffisant** · Vous n'avez pas assez d'argent pour parier {amount}{currency}.", ephemeral=True)
        
        bet_message_link = f"https://discord.com/channels/{channel.guild.id}/{channel.id}/{data['message_id']}"
        bet_message_button = discord.ui.Button(label="Voir l'affichage", url=bet_message_link)
        bet_message_view = discord.ui.View()
        bet_message_view.add_item(bet_message_button)
        
        current_bet = self.get_bet(channel, interaction.user)
        if not current_bet: # Aucun pari réalisé
            self.set_bet(channel, interaction.user, choice, amount)
            account.withdraw(amount, reason=f"Pari sur {data['title']}")

            await interaction.followup.send(f"**Pari enregistré** · Vous avez parié {amount}{currency} sur `{choice.capitalize()}`.", view=bet_message_view)
        elif current_bet['choice'] == choice: # Même choix, il peut ajouter de l'argent
            
            # Message de confirmation
            view = ConfirmationView(custom_labels=('Ajouter', 'Annuler'))
            await interaction.followup.send(f"**Pari déjà enregistré** · Vous avez déjà parié {current_bet['amount']}{currency} sur `{choice.capitalize()}`.\nVoulez-vous ajouter {amount}{currency} à votre pari ?", view=view, ephemeral=True)
            await view.wait()
            if view.value is None:
                return await interaction.edit_original_response(content="**Ajout annulé** · Vous n'avez pas répondu à temps.", view=None)
            elif view.value is False:
                return await interaction.edit_original_response(content=f"**Ajout annulé** · Vous n'avez pas ajouté {amount}{currency} à votre pari.", view=None)
            
            new_bet = current_bet['amount'] + amount
            account.withdraw(amount, reason=f"Pari sur {data['title']}")
            self.set_bet(channel, interaction.user, choice, new_bet)
            
            await interaction.edit_original_response(content=f"**Pari mis à jour** · Vous avez ajouté {new_bet:+}{currency} sur `{choice.capitalize()}`.\nAu total, vous avez parié {new_bet}{currency} dessus.", view=None)
        else: # Il ne peut pas changer de choix
            return await interaction.followup.send(f"**Pari déjà enregistré** · Vous avez déjà parié {current_bet['amount']}{currency} sur `{current_bet['choice'].capitalize()}`.", ephemeral=True)
        await self.update_display(channel)
    
    @_bet_choice.autocomplete('choice')
    @_stop_gamble.autocomplete('result')
    async def _choice_autocomplete(self, interaction: discord.Interaction, current: str):
        if not isinstance(interaction.channel, (discord.TextChannel, discord.Thread)):
            return []
        data = self.get_betting(interaction.channel)
        if not data:
            return []
        r = fuzzy.finder(current, [c for c in data['choices'].split(',')])
        return [app_commands.Choice(name=c.capitalize(), value=c) for c in r]
    
async def setup(bot):
    cog = Gambling(bot)
    await bot.add_cog(cog)
