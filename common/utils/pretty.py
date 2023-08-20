# Fonctions d'affichage transverses
from typing import Union
from datetime import datetime, timedelta

def bar_chart(value: int | float, total: int | float, *, lenght: int = 10, use_half_bar: bool = True, display_percent: bool = False) -> str:
    """Retourne un diagramme en barres

    :param value: Valeur à représenter
    :param total: Valeur maximale possible
    :param lenght: Longueur du diagramme, par défaut 10 caractères
    :param use_half_bar: S'il faut utiliser des demi-barres pour les valeurs intermédiaires, par défaut True
    :param display_percent: S'il faut afficher le pourcentage en fin de barre, par défaut False
    :return: str
    """
    if total == 0:
        return ' '
    percent = (value / total) * 100
    nb_bars = percent / (100 / lenght)
    bars = '█' * int(nb_bars)
    if (nb_bars % 1) >= 0.5 and use_half_bar:
        bars += '▌'
    if display_percent:
        bars += f' {round(percent)}%'
    return bars

def troncate_text(text: str, length: int, add_ellipsis: bool = True) -> str:
    """Retourne une version tronquée du texte donné

    :param length: Nombre de caractères max. voulus
    :param add_ellipsis: S'il faut ajouter ou non '…' lorsque le message est tronqué, par défaut True
    :return: str
    """
    if len(text) <= length:
        return text
    if add_ellipsis:
        length -= 1
    return text[:length] + '…' if add_ellipsis else ''
    
def humanize_number(number: Union[int, float], separator: str = ' ') -> str:
    """Formatte un nombre pour qu'il soit plus lisible

    :param number: Nombre à formatter
    :param separator: Séparateur entre groupes de 3 chiffres, par défaut ' '
    :return: str
    """
    return f'{number:,}'.replace(',', separator)

def codeblock(text: str, lang: str = "") -> str:
    """Retourne le texte sous forme d'un bloc de code

    :param text: Texte à formatter
    :param lang: Langage à utiliser, par défaut "" (aucun)
    :return: str
    """
    return f"```{lang}\n{text}\n```"

def parse_time(delta: timedelta) -> str:
    """Renvoie un texte représentant la durée relative donnée"""
    seconds = delta.seconds + delta.days * 24 * 3600
    units = {
        'j': delta.days,
        'h': seconds // 3600 % 24,
        'm': seconds // 60 % 60,
        's': seconds % 60
    }
    trsl = {
        'j': ('jour', 'jours'),
        'h': ('heure', 'heures'),
        'm': ('minute', 'minutes'),
        's': ('seconde', 'secondes')
    }
    txt = ""
    for unit, value in units.items():
        if value > 0:
            txt += f"{value} {trsl[unit][0] if value == 1 else trsl[unit][1]} "
    
    return txt.strip()
