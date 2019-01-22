import torch
from torch import nn

from ..utils import get_dict_values
from .distributions import Distribution


class MixtureModel(Distribution):
    """
    Mixture models.
    :math:`p(x) = \sum_i p(x|z=i)p(z=i)`

    Parameters
    ----------
    distributions : list
        List of distributions.

    prior : pixyz.Distribution.Categorical
        Prior distribution of latent variable (i.e., the contribution rate).
        This should be a categorical distribution and
        the number of its category should be the same as the length of the distribution list.

    Examples
    --------
    >>> from pixyz.distributions import Normal, Categorical
    >>> from pixyz.distributions.mixture_distributions import MixtureModel
    >>>
    >>> z_dim = 3  # the number of mixture
    >>> x_dim = 2  # the input dimension.
    >>>
    >>> distributions = []  # the list of distributions
    >>> for i in range(z_dim):
    >>>     loc = torch.randn(x_dim)  # initialize the value of location (mean)
    >>>     scale = torch.empty(x_dim).fill_(1.)  # initialize the value of scale (variance)
    >>>     distributions.append(Normal(loc=loc, scale=scale, var=["x"], name="p_%d" %i))
    >>>
    >>> probs = torch.empty(z_dim).fill_(1. / z_dim)  # initialize the value of probabilities
    >>> prior = Categorical(probs=probs, var=["z"], name="prior")
    >>>
    >>> p = MixtureModel(distributions=distributions, prior=prior)
    """

    def __init__(self, distributions, prior, name="p"):
        if not isinstance(distributions, list):
            raise ValueError
        else:
            distributions = nn.ModuleList(distributions)

        if prior.distribution_name != "Categorical":
            raise ValueError("The prior must be the categorical distribution.")

        # check the number of mixture
        if len(prior.get_params()["probs"]) != len(distributions):
            raise ValueError("The number of its category must be the same as the length of the distribution list.")

        # check whether all distributions have the same variable.
        var_list = []
        for d in distributions:
            var_list += d.var
        var_list = list(set(var_list))

        if len(var_list) != 1:
            raise ValueError("All distributions must have the same variable.")

        hidden_var = prior.var

        super().__init__(var=var_list, name=name)

        self._distributions = distributions
        self._prior = prior

        self._hidden_var = hidden_var

    @property
    def prob_text(self):
        _prob_text = "{}({})".format(
            self._name, ','.join(self._var)
        )

        return _prob_text

    @property
    def prob_factorized_text(self):
        _mixture_prob_text = []
        for i, d in enumerate(self._distributions):
            _mixture_prob_text.append("{}({}|{}={}){}({}={})".format(
                d.name, self._var[0], self._hidden_var[0], i,
                self._prior.name, self._hidden_var[0], i
            ))

        _prob_text = ' + '.join(_mixture_prob_text)

        return _prob_text

    @property
    def distribution_name(self):
        return "Mixture Model"

    def get_posterior_probs(self, x_dict):
        # log p(z|x) = log p(x, z) - log p(x)
        loglike = self.log_likelihood_all_hidden(x_dict) - self.log_likelihood(x_dict)

        # p(z|x)
        return torch.exp(loglike)  # (num_mix, batch_size)

    def sample(self, batch_size=1, return_hidden=False, **kwargs):
        hidden_output = []
        var_output = []

        for i in range(batch_size):
            # sample from prior
            _hidden_output = self._prior.sample()[self._hidden_var[0]]
            hidden_output.append(_hidden_output)

            var_output.append(self._distributions[
                                      _hidden_output.argmax(dim=-1)].sample()[self._var[0]])

        output_dict = {self._var[0]: torch.cat(var_output, 0)}

        if return_hidden:
            output_dict.update({self._hidden_var[0]: torch.cat(hidden_output, 0)})

        return output_dict

    def log_likelihood_all_hidden(self, x_dict):
        """
        Estimate joint log-likelihood, log p(x, z), where input is `x`.

        Parameters
        ----------
        x_dict : dict
            Input variables (including `var`).

        Returns
        -------
        loglike : torch.Tensor
            dim=0 : the number of mixture
            dim=1 : the size of batch
        """

        log_likelihood_all = []

        _device = x_dict[self._var[0]].device
        eye_tensor = torch.eye(len(self._distributions)).to(_device)  # for prior

        for i, d in enumerate(self._distributions):
            # p(z=i)
            prior_loglike = self._prior.log_likelihood({self._hidden_var[0]: eye_tensor[i]})
            # p(x|z=i)
            loglike = d.log_likelihood(x_dict)
            # p(x, z=i)
            log_likelihood_all.append(loglike + prior_loglike)

        return torch.stack(log_likelihood_all, dim=0)  # (num_mix, batch_size)

    def log_likelihood(self, x_dict):
        """
        Estimate log-likelihood, log p(x).

        Parameters
        ----------
        x_dict : dict
            Input variables (including `var`).

        Returns
        -------
        loglike : torch.Tensor
            The log-likelihood value of x.
        """

        loglike = self.log_likelihood_all_hidden(x_dict)
        return torch.logsumexp(loglike, 0)

    def _log_likelihood_given_hidden(self, x_dict):
        # log p(x, z)
        visible_dict = get_dict_values(x_dict, self._var, return_dict=True)
        loglike_all_hidden = self.log_likelihood_all_hidden(visible_dict)

        hidden_sample_idx = get_dict_values(x_dict, self._hidden_var, return_dict=False)[0].argmax(dim=-1)
        loglike = loglike_all_hidden[hidden_sample_idx, torch.arange(len(hidden_sample_idx))]

        return loglike
