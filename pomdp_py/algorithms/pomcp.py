# We implement POMCP as described in the original paper
# Monte-Carlo Planning in Large POMDPs
# https://papers.nips.cc/paper/4031-monte-carlo-planning-in-large-pomdps
#
# One thing to note is that, in this algorithm, belief
# update happens as the simulation progresses. The new
# belief is stored in the vnodes at the level after
# executing the next action. These particles will
# be reinvigorated if they are not enough.
#     However, it is possible to separate MCTS completely
# from the belief update. This means the belief nodes
# no longer keep track of particles, and belief update
# and particle reinvogration happen for once after MCTS
# is completed. I have previously implemented this version.
# This version is also implemented in BasicPOMCP.jl
# (https://github.com/JuliaPOMDP/BasicPOMCP.jl)
# The two should be EQUIVALENT. In general, it doesn't
# hurt to do the belief update during MCTS, a feature
# of using particle representation.

from abc import ABC, abstractmethod 
from pomdp_py.framework.planner import Planner
from pomdp_py.representations.distribution.particles import Particles
from pomdp_py.algorithms.po_uct import *
import copy
import time
import random
import math


class VNodeParticles(VNode):
    """POMCP's VNode maintains particle belief"""
    def __init__(self, num_visits, value, belief=Particles([])):
        self.num_visits = num_visits
        self.value = value
        self.belief = belief
        self.children = {}  # a -> QNode
    def __str__(self):
        return "VNode(%.3f, %.3f, %d | %s)" % (self.num_visits, self.value, len(self.belief),
                                               str(self.children.keys()))
    def __repr__(self):
        return self.__str__()

class RootVNodeParticles(RootVNode, VNodeParticles):
    def __init__(self, num_visits, value, history, belief=Particles([])):
        VNodeParticles.__init__(self, num_visits, value, belief)
        self.history = history
    @classmethod
    def from_vnode(cls, vnode, history):
        rootnode = RootVNodeParticles(vnode.num_visits, vnode.value, history, belief=vnode.belief)
        rootnode.children = vnode.children
        return rootnode

class POMCP(POUCT):

    """This POMCP version only works for problems
    with action space that can be enumerated."""

    def __init__(self,
                 max_depth=5, planning_time=1.,
                 discount_factor=0.9, exploration_const=math.sqrt(2),
                 num_visits_init=1, value_init=0,
                 rollout_policy=random_rollout,
                 action_prior=None):
        """
        rollout_policy(vnode, state=?) -> a; default random rollout.
        action_prior (ActionPrior), see above.
        """
        super().__init__(max_depth=max_depth,
                         planning_time=planning_time,
                         discount_factor=discount_factor,
                         exploration_const=exploration_const,
                         num_visits_init=num_visits_init,
                         value_init=value_init,
                         rollout_policy=rollout_policy,
                         action_prior=action_prior)

    @property
    def update_agent_belief(self):
        """True if planner's update function also updates agent's
        belief."""
        return True

    def plan(self, agent, action_prior_args={}):        
        # Only works if the agent's belief is particles
        if not isinstance(agent.belief, Particles):
            raise TypeError("Agent's belief is not represented in particles.\n"\
                            "POMCP not usable. Please convert it to particles.")
        return POUCT.plan(self, agent, action_prior_args=action_prior_args)

    def update(self, agent, real_action, real_observation, action_prior_args={},
               state_transform_func=None):
        """
        Assume that the agent's history has been updated after taking real_action
        and receiving real_observation.

        `state_transform_func`: Used to add artificial transform to states during
            particle reinvigoration. Signature: s -> s_transformed
        """
        if not isinstance(agent.belief, Particles):
            raise TypeError("Agent's belief is not represented in particles.\n"\
                            "POMCP not usable. Please convert it to particles.")
        if not hasattr(agent, "tree"):
            print("Warning: agent does not have tree. Have you planned yet?")
            return
        
        if agent.tree[real_action][real_observation] is None:
            # Never anticipated the real_observation. No reinvigoration can happen.
            raise ValueError("Particle deprivation.")
        # Update the tree; Reinvigorate the tree's belief and use it
        # as the updated belief for the agent.
        agent.tree = RootVNodeParticles.from_vnode(agent.tree[real_action][real_observation],
                                                   agent.history)
        tree_belief = agent.tree.belief
        agent.set_belief(self._particle_reinvigoration(tree_belief,
                                                       real_action,
                                                       real_observation,
                                                       len(agent.init_belief.particles),
                                                       state_transform_func=state_transform_func))
        if agent.tree is None:
            # observation was never encountered in simulation.
            agent.tree = RootVNodeParticles(self._num_visits_init,
                                            self._value_init,
                                            copy.deepcopy(agent.belief),
                                            agent.history)
            self._expand_vnode(agent.tree, agent.history,
                               action_prior_args=action_prior_args)
        else:
            agent.tree.belief = copy.deepcopy(agent.belief)

    def _particle_reinvigoration(self, particles, real_action,
                                 real_observation, num_particles, state_transform_func=None):
        """Note that particles should contain states that have already made
        the transition as a result of the real action. Therefore, they simply
        form part of the reinvigorated particles. At least maintain `num_particles`
        number of particles. If already have more, then it's ok.
        """
        # If not enough particles, introduce artificial noise to existing particles (reinvigoration)
        new_particles = copy.deepcopy(particles)
        if len(new_particles) == 0:
            raise ValueError("Particle deprivation.")

        if len(new_particles) > num_particles:
            return new_particles
        
        print("Particle reinvigoration for %d particles" % (num_particles - len(new_particles)))
        while len(new_particles) < num_particles:
            # need to make a copy otherwise the transform affects states in 'particles'
            next_state = copy.deepcopy(particles.random())
            # Add artificial noise
            if state_transform_func is not None:
                next_state = state_transform_func(next_state)
            new_particles.add(next_state)
        return new_particles

    def _expand_vnode(self, vnode, history, state=None, action_prior_args={}):
        POUCT._expand_vnode(self, vnode, history, state=state,
                            action_prior_args={"belief": vnode.belief})

    def _sample_belief(self, agent):
        return self._agent.tree.belief.random()                    

    def _simulate(self, state, history, root, parent, observation, depth, action_prior_args={}):
        total_reward = POUCT._simulate(self, state, history, root, parent, observation, depth, action_prior_args={})
        if depth == 1 and root is not None:
            root.belief.add(state)  # belief update happens as simulation goes.
        return total_reward

    def _VNode(self, agent=None, root=False, **kwargs):
        """Returns a VNode with default values; The function naming makes it clear
        that this function is about creating a VNode object."""
        if root:
            # agent cannot be None.
            return RootVNodeParticles(self._num_visits_init,
                                      self._value_init,
                                      agent.history,
                                      belief=copy.deepcopy(agent.belief))
        else:
            if agent is None:
                return VNodeParticles(self._num_visits_init,
                                      self._value_init,
                                      belief=Particles([]))
            else:
                return VNodeParticles(self._num_visits_init,
                                      self._value_init,
                                      belief=copy.deepcopy(agent.belief))
