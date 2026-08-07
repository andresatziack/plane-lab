# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Money arithmetic for work logs and overage: the value in reais.

Section 3 and section 6 of the pricing phase brief, plus the monetary half of section
4b of the master context, live here and nowhere else.

**This module imports nothing from Django, deliberately -- not even models.** It is the
second module in the feature with that property, and the reason is the same one
``plane.utils.service_log_time`` gives for itself: these functions decide what a client
is charged, so they must be testable without a database, a settings module or a request.

It is a **separate module from ``service_log_time`` rather than an addition to it**,
because that module's name would then be a lie. Hours and reais are different scales
with different rounding obligations, and the one place they meet -- turning hours into a
value -- is here.

Naming note: the phase brief spells the formulas in Portuguese (``valor``,
``horas_equivalentes``, ``horas_apontadas``, ``valor_hora_base``). They are English
here, following the convention Phases 1 and 2 established and that DECISOES.md settled
by realigning the documents to the code. The mapping is noted on each function.
"""

# Python imports
from decimal import ROUND_HALF_UP, Decimal

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Scale of every monetary quantity, fixed by section 4b of the master context: (12, 2).
#:
#: Two places because reais have two, and no more: a "fractional cent" carried through a
#: report is a number that cannot appear on an invoice, and the moment one exists someone
#: has to decide where it goes. Section 4b's answer is that it never exists, because the
#: rounding happens once per work log -- see ``quantize_money``.
#:
#: NOT the hour scale. An hour column is (10, 4) and a money column is (12, 2); the
#: product of the two is six decimal places, so every place a rate meets an hour figure
#: has to quantise explicitly. Both functions below do.
MONEY_SCALE = Decimal("0.01")

ZERO_MONEY = Decimal("0.00")


def quantize_money(value):
    """Any monetary figure at the one scale section 4b fixes.

    **``ROUND_HALF_UP`` explicitly, and the explicitness is the point.**
    ``quantize_hours`` omits the ``rounding`` argument and inherits the decimal
    context's default, which is ``ROUND_HALF_EVEN`` (banker's rounding). That is
    harmless there because an hour figure is exact by construction -- a multiple of 15
    minutes times a two-place multiplier terminates within four places, so the quantise
    never actually rounds anything.

    Here it rounds for real, and **genuine ties are reachable and commonplace**: an hour
    figure is a multiple of ``0.25`` and a rate has two places, so the product can land
    exactly on a half-cent. ``0.25h * R$ 180,50 = 45.125000`` -- half-up gives
    ``R$ 45,13`` and half-even gives ``R$ 45,12``. Inheriting the context default would
    make the value of an invoice line depend on a global anyone can change, and would
    round half of those ties *down*, against the supplier. Section 4b requires half-up,
    so it is passed here and at no other call site, because nothing else quantises money.

    Applied once per work log, at the moment the value is derived. Never applied again to
    a total: rounding a sum of products is not the same number as summing rounded
    products, which is what acceptance criterion 13 -- "nenhum centavo perdido ou criado"
    -- is testing for.
    """
    return Decimal(value).quantize(MONEY_SCALE, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# The two pricing formulas -- section 3
# ---------------------------------------------------------------------------
#
# THESE ARE TWO FUNCTIONS, NOT ONE FUNCTION WITH A FLAG, AND THEY MULTIPLY DIFFERENT
# HOUR QUANTITIES. That is the single most error-prone thing in this phase, so it is
# worth stating before either signature:
#
#   * the base rate multiplies EQUIVALENT hours, because the hour type's multiplier has
#     not yet entered the price -- it entered the hours;
#   * an absolute override multiplies LOGGED hours, because an absolute rate *replaces*
#     ``base * multiplier`` and therefore already embeds the multiplier.
#
# Acceptance criterion 5 is where this bites: an override of R$ 350,00 for "Domingos e
# feriados" (multiplier 2.0), one hour worked on a Sunday. Logged is 1.0000 and
# equivalent is 2.0000. The correct answer is 1.0 * 350 = R$ 350,00. Reaching for
# ``equivalent_hours`` out of habit gives R$ 700,00 -- a double charge, which is
# acceptance criterion 8's bug wearing criterion 5's clothes, in a place the brief does
# not warn about.
#
# Two separately named functions with keyword-only, differently named parameters is the
# structural defence: passing the wrong quantity requires writing the wrong parameter
# name, which fails loudly, whereas a boolean flag in the wrong position is invisible at
# the call site and silently correct-looking.


def amount_from_base_rate(*, equivalent_hours, base_hour_rate):
    """Value of a work log priced from the client's base rate. Section 3, first formula.

    ``valor = horas_equivalentes * valor_hora_base_do_cliente``

    Takes **equivalent** hours: the multiplier is already inside them, which is why
    acceptance criteria 1, 2 and 3 all fall out of one multiplication -- 1h commercial at
    R$ 200 is R$ 200,00, 1h after hours (1.5x) is 1.5 * 200 = R$ 300,00, and 1h15 of
    commercial time is 1.25 * 200 = R$ 250,00.
    """
    return quantize_money(Decimal(equivalent_hours) * Decimal(base_hour_rate))


def amount_from_absolute_rate(*, logged_hours, absolute_rate):
    """Value of a work log priced from an hour type's absolute override. Section 3.

    ``valor = horas_apontadas * valor_absoluto_do_tipo``

    Takes **logged** hours -- the chronological time, before the multiplier. See the
    block comment above: an absolute rate replaces ``base * multiplier``, so applying it
    to equivalent hours charges the multiplier twice.
    """
    return quantize_money(Decimal(logged_hours) * Decimal(absolute_rate))


def overage_amount(*, overage_hours, overage_hour_rate):
    """Value of a billed overage, for a contract period or a work item allowance.

    ``valor = horas_excedentes * valor_hora_de_excedente``

    Acceptance criterion 7: a 3h deficit at R$ 250,00/h is R$ 750,00.

    **There is no multiplier parameter, and its absence is the defence for acceptance
    criterion 8.** ``ServiceContractPeriod.overage_hours`` and
    ``ServiceIssueAllowance.overage_hours`` are both accumulated from
    ``ServiceLog.debited_hours``, which is already equivalent -- the multiplier entered
    when the work log was created. Applying it again here would double-charge every
    after-hours overage. The signature makes that impossible to do by accident: there is
    no argument to pass a multiplier through, so committing the bug means changing the
    function rather than mis-ordering a call.
    """
    return quantize_money(Decimal(overage_hours) * Decimal(overage_hour_rate))


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------


def format_money(value):
    """A monetary figure as pt-BR currency, e.g. ``"R$ 1.234,56"``.

    Formatted here rather than in the browser for the same reason ``format_hours`` is:
    the API and the CSV export must agree with each other and with the screen, and three
    independent formatters is three chances to disagree about a thousands separator.

    Always two decimal places, including trailing zeros -- unlike ``format_hours``, which
    strips them. ``R$ 200`` is a price written carelessly; ``R$ 200,00`` is a price.
    """
    quantized = quantize_money(value)

    # Build the grouping by hand: `locale` depends on system locales being installed,
    # which is not something a container image guarantees, and a missing pt_BR locale
    # would silently fall back to a dot decimal separator on an invoice.
    sign = "-" if quantized < 0 else ""
    digits = f"{abs(quantized):.2f}"
    whole, cents = digits.split(".")

    grouped = ""
    for index, digit in enumerate(reversed(whole)):
        if index and index % 3 == 0:
            grouped = "." + grouped
        grouped = digit + grouped

    return f"{sign}R$ {grouped},{cents}"
