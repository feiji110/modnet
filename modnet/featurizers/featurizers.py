import abc
from typing import Optional, Iterable, Tuple, Dict

import pandas as pd

from matminer.featurizers.base import MultipleFeaturizer, BaseFeaturizer
from matminer.featurizers.structure import SiteStatsFingerprint
from matminer.featurizers.conversions import CompositionToOxidComposition

from modnet.utils import LOG


__all__ = ("MODFeaturizer",)


class MODFeaturizer(abc.ABC):
    """Base class for multiple featurization across
    structure, composition and sites.

    Child classes must provide iterables of matminer featurizer objects
    to be applied to the structure, composition and sites of the structures
    in the input dataframe.

    Attributes:
        composition_featurizers: Optional iterable of  featurizers to
            apply to the 'composition' column (which will be generated
            if missing).
        oxid_composition_featurizers: Optional iterable of featurizers
            to apply to the 'composition_oxid' column generated by the
            `CompositionToOxidComposition` converter.
        structure_featurizers: Optional iterable of featurizers to apply
            to the structure as `SiteStatsFingerprint` objects. Uses
            the `site_stats` attribute to determine which statistics are
            calculated.
        site_stats: Iterable of string statistic names to be used by the
            `SiteStatsFingerprint` objects.
        featurizer_mode: Whether or not to apply all featurizers at once
            ("multi"), i.e., parallelising over structures, or one-at-a-time
            ("single"), i.e., parallelising over featurisers.

    """

    composition_featurizers: Optional[Iterable[BaseFeaturizer]] = None
    oxid_composition_featurizers: Optional[Iterable[BaseFeaturizer]] = None
    structure_featurizers: Optional[Iterable[BaseFeaturizer]] = None
    site_featurizers: Optional[Iterable[BaseFeaturizer]] = None
    site_stats: Tuple[str] = ("mean", "std_dev")
    featurizer_mode: str = "multi"

    def __init__(self, n_jobs=None):
        """Initialise the MODFeaturizer object with a requested
        number of threads to use during featurization.

        Arguments:
            n_jobs: The number of threads to use. If `None`, matminer
            will use `multiprocessing.cpu_count()` by default.

        """
        self.set_n_jobs(n_jobs)

    def set_n_jobs(self, n_jobs: Optional[int]):
        """Set the no. of threads to pass to matminer for featurizer
        initialisation.

        Arguments:
            n_jobs: The number of threads to use. If `None`, matminer
            will use `multiprocessing.cpu_count()` by default.

        """
        self._n_jobs = n_jobs

    def featurize(self, df: pd.DataFrame) -> pd.DataFrame:
        """Run all of the preset featurizers on the input dataframe.

        Arguments:
            df: the input dataframe with a `"structure"` column
                containing pymatgen `Structure` objects.

        Returns:
            The featurized DataFrame.

        """
        df_composition = pd.DataFrame([])
        if self.composition_featurizers or self.oxid_composition_featurizers:
            df_composition = self.featurize_composition(df)

        df_structure = pd.DataFrame([])
        if self.structure_featurizers:
            df_structure = self.featurize_structure(df)

        df_site = pd.DataFrame([])
        if self.site_featurizers:
            df_site = self.featurize_site(df)

        return df_composition.join(df_structure.join(df_site, lsuffix="l"), rsuffix="r")

    def _fit_apply_featurizers(
        self,
        df: pd.DataFrame,
        featurizers: Iterable[BaseFeaturizer],
        column: str,
        fit_to_df: bool = True,
        mode: str = "single",
    ) -> pd.DataFrame:
        """For the list of featurizers, fit each to the chosen column of
        the input pd.DataFrame and then apply them as a MultipleFeaturizer.

        Arguments:
            df: The DataFrame to featurize.
            featurizers: The list of matminer featurizers to fit and apply
                to the DataFrame.
            column: The name of the column to apply the featurizers to.
            fit_to_df: Whether or not to fit the featurizers to the
                input dataframe. If not true, it will be assumed that
                any featurizers that required fitting have already been
                fitted.
            mode: either 'multi' or 'single' to indicate whether featurizers
                will be applied all at once, or one at a time (useful for timing).

        Returns:
            pandas.DataFrame: the decorated DataFrame.

        """
        import time

        LOG.info(f"Applying featurizers {featurizers} to column {column!r}.")
        if mode == "multi":
            if fit_to_df:
                _featurizers = MultipleFeaturizer(
                    [feat.fit(df[column]) for feat in featurizers]
                )
            else:
                _featurizers = MultipleFeaturizer(featurizers)
            if self._n_jobs is not None:
                _featurizers.set_n_jobs(self._n_jobs)

            return _featurizers.featurize_dataframe(
                df, column, multiindex=True, ignore_errors=True
            )
        elif mode == "single":

            for featurizer in featurizers:

                if column not in df:
                    column = ("Input Data", column)
                start = time.monotonic_ns()
                if fit_to_df:
                    LOG.info(
                        f"Fitting featurizer {featurizer.__class__.__name__} to column {column!r}."
                    )
                    featurizer = featurizer.fit(df[column])
                    LOG.info(
                        f"Fitted featurizer {featurizer.__class__.__name__} to column {column!r} in {(time.monotonic_ns() - start) * 1e-9} seconds"
                    )
                featurizer.set_n_jobs(1)
                LOG.info(
                    f"Applying featurizer {featurizer.__class__.__name__} to column {column!r}."
                )
                start = time.monotonic_ns()
                df = featurizer.featurize_dataframe(
                    df, column, multiindex=True, ignore_errors=True
                )
                LOG.info(
                    f"Applied featurizer {featurizer.__class__.__name__} to column {column!r} in {(time.monotonic_ns() - start) * 1e-9} seconds"
                )
            LOG.info("Applied all featurizers")

            return df

        else:
            raise RuntimeError(f"`mode` must be 'multi' or 'single', not {mode}.")

    def featurize_composition(self, df: pd.DataFrame) -> pd.DataFrame:
        """Decorate input `pandas.DataFrame` of structures with composition
        features from matminer, specified by the MODFeaturizer preset.

        Currently applies the set of all matminer composition features.

        Arguments:
            df: the input dataframe with a `"structure"` column
                containing pymatgen `Structure` objects.

        Returns:
            pandas.DataFrame: the decorated DataFrame, or an empty
                DataFrame if no composition/oxidation featurizers
                exist for this class.

        """

        df = df.copy()

        if self.composition_featurizers:

            LOG.info("Applying composition featurizers...")
            df["composition"] = df["structure"].apply(lambda s: s.composition)
            df = self._fit_apply_featurizers(
                df,
                self.composition_featurizers,
                "composition",
                mode=self.featurizer_mode,
            )
            df = df.rename(columns={"Input Data": ""})
            df.columns = df.columns.map("|".join).str.strip("|")

        if self.oxid_composition_featurizers:
            LOG.info("Applying oxidation state featurizers...")
            if getattr(self, "fast_oxid", False):
                df = CompositionToOxidComposition(
                    all_oxi_states=False, max_sites=-1
                ).featurize_dataframe(df, "composition")
            else:
                df = CompositionToOxidComposition().featurize_dataframe(
                    df, "composition"
                )
            df = self._fit_apply_featurizers(
                df,
                self.oxid_composition_featurizers,
                "composition_oxid",
                mode=self.featurizer_mode,
            )
            df = df.rename(columns={"Input Data": ""})
            df.columns = df.columns.map("|".join).str.strip("|")

        return df

    def featurize_structure(self, df: pd.DataFrame) -> pd.DataFrame:
        """Decorate input `pandas.DataFrame` of structures with structural
        features from matminer, specified by the MODFeaturizer preset.

        Currently applies the set of all matminer structure features.

        Arguments:
            df: the input dataframe with a `"structure"` column
                containing pymatgen `Structure` objects.

        Returns:
            pandas.DataFrame: the decorated DataFrame.

        """

        LOG.info("Applying structure featurizers...")
        df = df.copy()
        df = self._fit_apply_featurizers(
            df, self.structure_featurizers, "structure", mode=self.featurizer_mode
        )
        df.columns = df.columns.map("|".join).str.strip("|")

        return df

    def featurize_site(
        self, df: pd.DataFrame, aliases: Optional[Dict[str, str]] = None
    ) -> pd.DataFrame:
        """Decorate input `pandas.DataFrame` of structures with site
        features, specified by the MODFeaturizer preset.

        Arguments:
            df: the input dataframe with a `"structure"` column
                containing pymatgen `Structure` objects.
            aliases: optional dictionary to map matminer output column
                names to new aliases, mostly used for
                backwards-compatibility.

        Returns:
            pandas.DataFrame: the decorated DataFrame.

        """

        LOG.info("Applying site featurizers...")

        df = df.copy()
        df.columns = ["Input data|" + x for x in df.columns]

        for fingerprint in self.site_featurizers:
            site_stats_fingerprint = SiteStatsFingerprint(
                fingerprint, stats=self.site_stats
            )
            df = site_stats_fingerprint.featurize_dataframe(
                df, "Input data|structure", multiindex=False, ignore_errors=True
            )

            fingerprint_name = fingerprint.__class__.__name__
            if aliases:
                fingerprint_name = aliases.get(fingerprint_name, fingerprint_name)
            if "|" not in fingerprint_name:
                fingerprint_name += "|"
            df.columns = [
                f"{fingerprint_name}{x}" if "|" not in x else x for x in df.columns
            ]

        return df
