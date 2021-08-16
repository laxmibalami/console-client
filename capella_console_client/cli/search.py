import os
from typing import List, Dict, Any, Optional
import json

import typer
import questionary
from tabulate import tabulate

from capella_console_client.enumerations import BaseEnum
from capella_console_client.config import (
    STAC_PREFIXED_BY_QUERY_FIELDS,
)
from capella_console_client.enumerations import (
    InstrumentMode,
    ProductClass,
    ObservationDirection,
    OrbitState,
    OrbitalPlane,
    ProductType,
)
from capella_console_client.cli.client_singleton import CLIENT
from capella_console_client.cli.validate import (
    get_validator,
    get_caster,
    _validate_out_path,
    _no_selection_bye,
    _at_least_one_selected,
)
from capella_console_client.cli.cache import CLICache
from capella_console_client.cli.config import (
    CLI_SUPPORTED_SEARCH_FILTERS,
    CURRENT_SETTINGS,
    PROMPT_OPERATORS,
)
from capella_console_client.cli.user_searches.my_search_results import _load_and_prompt
from capella_console_client.cli.visualize import show_tabulated
from capella_console_client.cli.settings import _prompt_search_result_headers


app = typer.Typer(help="search STAC items")


# TODO: autocomplete option
@app.command()
def interactive():
    """
    interactive search with prompts
    """
    search_kwargs = _prompt_search_filter()
    stac_items = CLIENT.search(**search_kwargs)

    if stac_items:
        show_tabulated(stac_items)
        _prompt_post_search_actions(stac_items, search_kwargs)


class STACSearchQuery(dict):
    def __str__(self):
        _filters = []
        for k, v in self.items():
            if isinstance(v, list):
                cur = f"{k}{'|'.join(v)}"
            else:
                cur = f"{k}{v}"
            _filters.append(cur)

        return "-".join(_filters)


def _prompt_search_operator(field: str) -> Dict[str, str]:

    operators = questionary.checkbox(
        f"{field}:", choices=["=", ">", ">=", "<", "<=", "in"]
    ).ask()
    _no_selection_bye(operators, "Please select at least one search operator")

    ops_map = {
        "=": "eq",
        ">": "gt",
        ">=": "gte",
        "<": "lt",
        "<=": "lte",
        "in": "in",
    }
    return {o: ops_map[o] for o in operators}


def prompt_enum_choices(field: str) -> Optional[Dict[str, Any]]:
    enum_cls = {
        "instrument_mode": InstrumentMode,
        "observation_direction": ObservationDirection,
        "orbital_plane": OrbitalPlane,
        "orbit_state": OrbitState,
        "product_category": ProductClass,
        "product_type": ProductType,
    }.get(field)

    if not enum_cls:
        return None

    choices = questionary.checkbox(
        f"{field}:", choices=[e.value for e in enum_cls]  # type: ignore
    ).ask()
    _no_selection_bye(choices)
    return {field: choices}


def _prompt_operator_value(field: str, search_op: str, operator_map: Dict[str, str]):
    suffix = f"[{search_op}]" if search_op is not None else ""
    str_val = questionary.text(
        message=f"{field} {suffix}:",
        validate=get_validator(field),
    ).ask()

    # optional cast from str
    cast_fct = get_caster(field)
    field_desc = f"{field}__{operator_map[search_op]}" if search_op else field

    if cast_fct:
        return {field_desc: cast_fct(str_val)}
    return {field_desc: str_val}


def _prompt_search_filter() -> STACSearchQuery:
    search_filter_names = questionary.checkbox(
        "What are you looking for today?",
        choices=CLI_SUPPORTED_SEARCH_FILTERS,
        validate=_at_least_one_selected,
    ).ask()
    _no_selection_bye(
        search_filter_names, "Please select at least one search condition"
    )

    search_kwargs = STACSearchQuery()

    for field in search_filter_names:
        cur_search_payload = prompt_enum_choices(field)

        # enum takes precedence
        if cur_search_payload:
            search_kwargs.update(cur_search_payload)
            continue

        if field in PROMPT_OPERATORS:
            operator_map = _prompt_search_operator(field)
        else:
            operator_map = {"": ""}

        for search_op in operator_map:
            cur_query = _prompt_operator_value(field, search_op, operator_map)
            search_kwargs.update(cur_query)

    if "limit" not in search_kwargs:
        search_kwargs["limit"] = CURRENT_SETTINGS["limit"]
    return search_kwargs


class PostSearchActions(str, BaseEnum):
    adjust_headers = "change result table headers"
    save_current_search = ("save search query and result for reuse",)
    export_json = "export search result as .json"
    quit = "quit"

    @classmethod
    def save_search(
        cls, stac_items: List[Dict[str, Any]], search_kwargs: STACSearchQuery
    ):
        identifier = questionary.text(
            message="Please provide an identifier for your search",
            default=str(search_kwargs),
            validate=lambda x: x is not None and len(x) > 0,
        ).ask()
        stac_ids = [i["id"] for i in stac_items]
        CLICache.update_my_search_results(identifier, stac_ids)
        CLICache.update_my_search_queries(identifier, search_kwargs)  # type: ignore

        txt = f"""Added '{identifier}' to my-search-results and my-search-queries"
Issue

\tcapella-console-wizard my-search-results list

    or

\tcapella-console-wizard my-search-queries list

in order to list your saved search results or queries.
"""

        typer.echo(txt)

    @classmethod
    def export_search(
        cls, stac_items: List[Dict[str, Any]], search_kwargs: STACSearchQuery
    ) -> str:
        default = CURRENT_SETTINGS["out_path"]
        if default[-1] != os.sep:
            default += os.sep
        default += f"{search_kwargs}.json"

        path = questionary.path(
            message="Please provide path and filename where you want to save the stac items .json",
            default=default,
            validate=_validate_out_path,
        ).ask()
        _no_selection_bye(path, "Please provide a path")

        with open(path, "w") as fp:
            json.dump(stac_items, fp)
        typer.echo(f"Saved {len(stac_items)} STAC items to {path}")
        return path


def _prompt_post_search_actions(
    stac_items: List[Dict[str, Any]], search_kwargs: STACSearchQuery, no_save=False
):
    choices = list(PostSearchActions)
    if no_save:
        del choices[choices.index(PostSearchActions.save_current_search)]

    action_selection = None
    while action_selection != PostSearchActions.quit:
        action_selection = questionary.select(
            "Anything you'd like to do now?",
            choices=choices,
        ).ask()
        _no_selection_bye(action_selection)

        if action_selection == PostSearchActions.adjust_headers:
            search_headers = _prompt_search_result_headers()
            CURRENT_SETTINGS["search_headers"] = search_headers
            show_tabulated(stac_items, search_headers)

        if action_selection == PostSearchActions.save_current_search:
            PostSearchActions.save_search(stac_items, search_kwargs)
            del choices[choices.index(PostSearchActions.save_current_search)]

        if action_selection == PostSearchActions.export_json:
            path = PostSearchActions.export_search(stac_items, search_kwargs)
            if questionary.confirm("Would you like to open it?").ask():
                os.system(f"open {path}")


@app.command()
def from_saved():
    """
    select and show STAC items of saved search
    """
    saved_search_results, selection = _load_and_prompt(
        "Which saved search would you like to use?", multiple=False
    )
    stac_items = CLIENT.search(ids=saved_search_results[selection])

    if stac_items:
        show_tabulated(stac_items)
        _prompt_post_search_actions(stac_items, selection, no_save=True)
