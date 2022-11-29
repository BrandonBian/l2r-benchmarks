"""This is OpenAI' Spinning Up PyTorch implementation of Soft-Actor-Critic with
minor adjustments.
For the official documentation, see below:
https://spinningup.openai.com/en/latest/algorithms/sac.html#documentation-pytorch-version
Source:
https://github.com/openai/spinningup/blob/master/spinup/algos/pytorch/sac/sac.py
"""
from email import policy
import itertools
from copy import deepcopy

import torch
import numpy as np
from gym.spaces import Box
from torch.optim import Adam

from src.agents.base import BaseAgent
from src.config.yamlize import yamlize, create_configurable, NameToSourcePath
from src.utils.utils import ActionSample, DebuggingRL

from src.constants import DEVICE


@yamlize
class SACAgent(BaseAgent):
    """Adopted from https://github.com/learn-to-race/l2r/blob/main/l2r/baselines/rl/sac.py"""

    def __init__(
        self,
        steps_to_sample_randomly: int,
        gamma: float,
        alpha: float,
        polyak: float,
        lr: float,
        actor_critic_cfg_path: str,
        load_checkpoint_from: str = "",
    ):
        """Initialize Soft Actor-Critic Agent

        Args:
            steps_to_sample_randomly (int): Number of steps to sample randomly
            gamma (float): Gamma parameter
            alpha (float): Alpha parameter
            polyak (float): Polyak parameter coef.
            lr (float): Learning rate parameter.
            actor_critic_cfg_path (str): Actor Critic Config Path
            load_checkpoint_from (str, optional): Load checkpoint from path. If '', then doesn't load anything. Defaults to ''.
        """

        super(SACAgent, self).__init__()

        self.steps_to_sample_randomly = steps_to_sample_randomly
        self.gamma = gamma
        self.alpha = alpha
        self.polyak = polyak
        self.load_checkpoint_from = load_checkpoint_from
        self.lr = lr

        self.t = 0
        self.deterministic = False

        self.record = {"transition_actor": ""}  # rename

        self.action_space = Box(-1, 1, (2,))
        self.act_dim = self.action_space.shape[0]
        self.obs_dim = 32

        self.actor_critic = create_configurable(
            actor_critic_cfg_path, NameToSourcePath.network
        )
        self.actor_critic.to(DEVICE)
        self.actor_critic_target = deepcopy(self.actor_critic)

        self.debugger = DebuggingRL()

        if self.load_checkpoint_from != "":
            self.load_model(self.load_checkpoint_from)

        self.q_params = itertools.chain(
            self.actor_critic.q1.parameters(), self.actor_critic.q2.parameters()
        )

        # Set up optimizers for policy and q-function
        self.pi_optimizer = Adam(self.actor_critic.policy.parameters(), lr=self.lr)
        self.q_optimizer = Adam(self.q_params, lr=self.lr)
        self.pi_scheduler = (
            torch.optim.lr_scheduler.StepLR(  # TODO: Call some scheduler in runner.
                self.pi_optimizer, 1, gamma=0.5
            )
        )

        # Freeze target networks with respect to optimizers (only update via polyak averaging)
        for p in self.actor_critic_target.parameters():
            p.requires_grad = False

    def select_action(self, obs):
        """Select action from obs.

        Args:
            obs (np.array): Observation to act on.

        Returns:
            ActionObj: Action object.
        """
        # Until start_steps have elapsed, randomly sample actions
        # from a uniform distribution for better exploration. Afterwards,
        # use the learned policy.
        action_obj = ActionSample()
        if self.t > self.steps_to_sample_randomly:
            a = self.actor_critic.act(obs.to(DEVICE), self.deterministic)
            a = a  # numpy array...
            action_obj.action = a
            self.record["transition_actor"] = "learner"
        else:
            a = self.action_space.sample()
            action_obj.action = a
            self.record["transition_actor"] = "random"
        self.t = self.t + 1
        return action_obj

    def register_reset(self, obs):
        """
        Same input/output as select_action, except this method is called at episodal reset.
        """
        pass

    def load_model(self, path):
        """Load model from path.

        Args:
            path (str): Load model from path.
        """
        self.actor_critic.load_state_dict(torch.load(path))

    def save_model(self, path):
        """Save model to path

        Args:
            path (str): Save model to path
        """
        torch.save(self.actor_critic.state_dict(), path)

    def _compute_loss_q(self, data, pi, log_pi):
        """Set up function for computing SAC Q-losses."""
        o, a, r, o2, d = (
            data["obs"],
            data["act"],
            data["rew"],
            data["obs2"],
            data["done"],
        )

        q1 = self.actor_critic.q1(o, a)
        q2 = self.actor_critic.q2(o, a)

        # Bellman backup for Q functions
        with torch.no_grad():
            # Target Q-values
            q1_pi_targ = self.actor_critic_target.q1(o2, pi)
            q2_pi_targ = self.actor_critic_target.q2(o2, pi)
            q_pi_targ = torch.min(q1_pi_targ, q2_pi_targ)
            # Calculates debug metrics of only one of the Q-nets, modify counter in function in utils to do both 
            resvar = self.debugger.residual_variance(q_pi_targ, q1) #resvar is calculated over multiple steps so it may be None most of the time
            self.debugger.collect_values_value_targets(value=q1, value_target=q1_pi_targ)
            backup = r + self.gamma * (1 - d) * (q_pi_targ - self.alpha * log_pi)

        # MSE loss against Bellman backup
        loss_q1 = ((q1 - backup) ** 2).mean()
        loss_q2 = ((q2 - backup) ** 2).mean()
        loss_q = loss_q1 + loss_q2

        # Useful info for logging
        q_info = dict(
            Q1Vals=q1.detach().cpu().numpy(), Q2Vals=q2.detach().cpu().numpy(), ResidualVariance=resvar
        )

        return loss_q, q_info

    def _compute_loss_pi(self, data, pi, logp_pi):
        """Set up function for computing SAC pi loss."""
        o = data["obs"]
        q1_pi = self.actor_critic.q1(o, pi)
        q2_pi = self.actor_critic.q2(o, pi)
        q_pi = torch.min(q1_pi, q2_pi)

        # Entropy-regularized policy loss
        loss_pi = (self.alpha * logp_pi - q_pi).mean()

        # Useful info for logging
        pi_info = dict(LogPi=logp_pi.detach().cpu().numpy())

        return loss_pi, pi_info

    def update(self, data):
        """Update SAC Agent given data

        Args:
            data (dict): Data from ReplayBuffer object.
        """

        policy_params = self.actor_critic.policy.parameters()
        q1_params = self.actor_critic.q1.parameters()
        q2_params = self.actor_critic.q1.parameters()
        mu, log_std = self.actor_critic.pi(data["obs"])
        #relpolent = self.debugger.relative_policy_entropy(log_std)
        # Entropy loss
        #self.alpha = torch.exp(self.log_ent_coef.detach())
        #ent_coef_loss = -(self.log_ent_coef * (logp_pi + self.target_entropy).detach()).mean()
        #self.ent_coef_optimizer.zero_grad()
        #ent_coef_loss.backward()
        #self.ent_coef_optimizer.step()

        # First run one gradient descent step for Q1 and Q2
        self.q_optimizer.zero_grad()
        loss_q, q_info = self._compute_loss_q(data, mu, log_std)
        loss_q.backward()
        self.q_optimizer.step()

        # Freeze Q-networks so you don't waste computational effort
        # computing gradients for them during the policy learning step.
        for p in self.q_params:
            p.requires_grad = False

        # Next run one gradient descent step for pi.

        self.pi_optimizer.zero_grad()
        loss_pi, _ = self._compute_loss_pi(data, mu, log_std)
        loss_pi.backward()
        self.pi_optimizer.step()

        # Unfreeze Q-networks so you can optimize it at next DDPG step.
        for p in self.q_params:
            p.requires_grad = True

        # Finally, update target networks by polyak averaging.
        with torch.no_grad():
            for p, p_targ in zip(
                self.actor_critic.parameters(), self.actor_critic_target.parameters()
            ):
                # NB: We use an in-place operations "mul_", "add_" to update target
                # params, as opposed to "mul" and "add", which would make new tensors.
                p_targ.data.mul_(self.polyak)
                p_targ.data.add_((1 - self.polyak) * p.data)

        updated_mu, updated_log_std = self.actor_critic.pi(data["obs"])
        
        kl_div = self.debugger.KLdivergence((mu, log_std), (updated_mu, updated_log_std))
        new_q1_params = self.actor_critic.q1.parameters()
        new_q2_params = self.actor_critic.q1.parameters()
        new_policy_params = self.actor_critic.policy.parameters()
        q1_absmaxs, q1_mse = self.debugger.step_stats(old_net_params=q1_params, new_net_params=new_q1_params, nettype="Q1 Value Net")
        q2_absmaxs, q2_mse = self.debugger.step_stats(old_net_params=q2_params, new_net_params=new_q2_params, nettype="Q2 Value Net")
        policy_absmaxs, policy_mse  = self.debugger.step_stats(old_net_params=policy_params, new_net_params=new_policy_params, nettype="Policy Net")
        
        return {
            "KL.Divergence" : kl_div,
            #"Relative.Policy.Entropy": relpolent,
            "Q1 Abs Max": q1_absmaxs,
            "Q1 MSE": q1_mse,
            "Q2 Abs Max": q2_absmaxs,
            "Q2 MSE": q2_mse,
            "Policy Abs Max": policy_absmaxs,
            "Policy MSE": policy_mse,
            "Residual Varaince": q_info["ResidualVaraince"]
            }