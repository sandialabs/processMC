#!/Library/Frameworks/Python.framework/Versions/6.3/Resources/Python.app/Contents/MacOS/Python
"""
Calculate the process matrix for a system given a Hamiltonian.

--
Latest revision:
Thursday 26 May 2011

Kevin Young, Ph.D.
Scalable and Secure Systems Research
Sandia National Laboratories
Livermore, CA
kyoung@sandia.gov
"""

import scipy
import random
from random import random, gauss
import os
import time
from matplotlib import pyplot, cm
from scipy import exp, sqrt, append, arange, mat, pi, linalg, array, dot, round_, real, kron, shape, log, linspace, unique, diff
from scipy.linalg import toeplitz, eig

# Here we include the tools for the parallization
# when using a multicore machine.
from multiprocessing import Process, Pipe
from itertools import izip
# Spawn a new process
def spawn(f):
    def fun(pipe,x):
        pipe.send(f(x))
        pipe.close()
    return fun
# A parallel map function that doesn't have the serialization
# problems of pmap, which is included with the multiprocessing module
def parmap(f,X):
    pipe=[Pipe() for x in X]
    proc=[Process(target=spawn(f),args=(c,x)) for x,(p,c) in izip(X,pipe)]
    [p.start() for p in proc]
    [p.join() for p in proc]
    return [p.recv() for (p,c) in pipe]

#  Diagnostic timing decorator (not for general use)
def timeit(method):
	def timed(*args, **kw):
		ts = time.time()
		result = method(*args, **kw)
		te = time.time()
		print te-ts
		return result
	return timed

# Define Pauli Matrices
"These define the unnormalized pauli matrices"
sigI = mat([[1.,0],[0,1.]])
sigX = mat([[0,1.],[1.,0]])
sigY = mat([[0,-1.j],[1.j,0]])
sigZ = mat([[1.,0],[0,-1.]])

# Build the maximum field given constraints
"""
Given a field*time area, maximum field strength, max dc/dt, and timestep,
construct the valid field with that timestep.
	>> cfield = build_max_field(pi, 1., 0.001, 0.01, offset = 0.)
This also returns the times at which the control changes values.  
"""
def build_max_field(area, cmax, delta_c, delta_t, offset = 1./2):
	n = floor(cmax/delta_c)
	a = n*(n+1)*delta_c*delta_t/2
	N = ceil((area-2*a)/(cmax*delta_t))
	q = (area - 2*a)/(N*delta_t)
	f = linspace(0,cmax,n+1)
	g = q*scipy.ones(N)
	h = linspace(cmax, 0, n+1)
	out_vec = append(hstack((f,g,h)),0)
	n_t = len(out_vec)
	times  = append(append([0],arange(0,n_t-2)+1./2),[n_t])*delta_t
	return [out_vec,times]

# Construct colored noise 
class Field():
	"""
	The Field class is a multipurpose class designed to be used in conjunction with the
	Liouvillian class.  All Hamiltonian parameters which can change with time should be
	instances of the Field class.  
	
	Usage:
		Define an instance of the Field class spanning a time t_final, descretized into
		n_steps.
		
			>> t_final = 1.
			>> n_steps = 1000
			>> myfield = Field(t_final, n_steps)
			
		At this step, myfield is a white noise process with power spectrum equal to 
		S(omega) = 0.  It can be converted to a white noise process with power spectrum 
		equal to, say, S(omega) = .35, as

			>> myfield.make_white(.35)
			
		...or pink noise process with power spectrum S(omega) = spectrum_amplitude / omega
		
			>> myfield.make_pink(spectrum_amplitude)

		...or an Orenstein-Uhlenbek process,
		
			>> myfield.make_ou(sigma, decay_rate)
			
		...or a gaussian noise process which is constant over the time t_final with variance sigma^2
		
			>> myfield.make_constant(sigma)
		
		...or a white noise process with gaussian baseline
		
			>> myfield.make_white_and_constant(white_sigma, constant_sigma)
			
		...or a process with a defined correlation function, such as C(t) = exp(-2.*t**2), by passing
		a function or lambda function
		
			>> myfield.make_defined(lambda t: exp(-2.*t**2))

		Once the noise process is defined, an instance of the noise can be generated by:
		
			>> myfield.make_noise()
			
		This produces a vector of n_steps entries.  It can be accessed by index, 
		
			>> myfield.get_noise(index)
			
		or by the time,
		
			>> myfield.get_timed(time)
		
		If time is outside the range [0,t_final], this will throw an exception.
		
		The Field() class is also used for any specified control fields.  The control 
		fields are defined as a vector,
		
			>> control_field = map(lambda t: sin(pi*t/t_final), lispace(0, t_final, n_steps))
			>> myfield.define_control(control_field)
		
		This noise can be subject to additive or multiplicative noise (or both)
		
			>> myfield.make_multiplicative()
			>> myfield.make_additive() 
		
		Or timing errors can be added.  Note that in the presence of timing errors, 
		no other noise source can be present in the Field.
		
			>> myfield.make_timing_error(timing_variance)
		
		This will modify the vector myfield.times every time that myfield.make_noise() is run.
		the myfield.get_timed_noise(time) responds to the timing jitter.

	"""
	# Initialize the class (a zero field if not otherwise specified)
	def __init__(self, t_final = 1., n_steps = 10**3, corrfn = 0. ):
		"""
		Create an instance of the noise.  t_final defaults to 1. and n_steps 
		defaults to 1000.  The correlation function defaults to 0.

			>> object = Field(t_final, n_steps)
			
		"""
		# add a control field if the noise is multiplicative
		self.control_field = scipy.zeros(n_steps)
		self.n_steps = n_steps
		self.t_final = t_final
		self.dt = t_final/n_steps
		self.corrfn = corrfn
		self.corrMat = 0.
		self.sigma = 0.
		# Define the correlation function (given in seconds)
		if type(corrfn)==float:
			# if you feed it a number, you get white noise with that strength
			self.make_white(corrfn)
		else:
			# you could also feed it a lambda function specifying the correlation function
			self.make_defined(corrfn)
			# self.corrfn = map(corrfn, scipy.linspace(0,t_final, n_steps))
			# self.noise_type = "defined"
		self.KL_calculated = False
		self.is_multiplicative = False
		self.is_additive = False
		self.control_defined = False
		self.timing_error = False
		self.times = linspace(0.,t_final,n_steps+1)
		self.name = "unnamed"
	
	def set_name(self, name):
		"""
		Give the process a name.

			>> name = "my_object_name"
			>> object.set_name(name)

		"""
		self.name = name
	
	def info(self):
		"""
		Print information about the process, including the process name, the first
		ten elements of the control field and time vector, whether the KL decomposition has
		been calculated, etc.
		
			>> object.info()
			
		"""


		print "Process Name:", self.name
		print "First 10 Control Fields", self.control_field[range(0,10)]
		print "Number of Steps:", self.n_steps
		print "Final Time:", self.t_final
		print "Noise Type:", self.noise_type
		print "KL Calculated:", self.KL_calculated
		print "Additive Noise:", self.is_additive
		print "Timing Jitter Noise:", self.timing_error
		print "First 10 Times:", self.times[range(0,10)]
	
	# Define a timing error	
	def make_timing_error(self, set_var=10**-8):
		"""
		Subject the control field to gaussian, white timing jitter with 
		variance equal to set_variance
		
			>> set_variance = 1.e-8
			>> object.make_timing_error(set_variance)
			
		"""
		self.timing_error = True
		self.timing_variance = set_var
		self.noise_type = 'timing'
	
	# Add a control field
	def define_control(self, control_field, *in_times):
		"""
		Define the control field.
		
			>> control_field = map(lambda t: sin(pi*t/t_final), linspace(0,t,n_steps))
			>> object.define_control(control_field)
			
		"""
		if in_times != ():
			self.input_times = in_times[0]
			self.times = in_times[0]
			# print self.times
			# print in_times
			self.t_final = self.times[-1]
		else:
			self.input_times = self.times
			
		if len(control_field) == self.n_steps:
			self.control_defined = True
			self.control_field = array(control_field)
		else:
			print "Input control field is length %d, must be length %d" % (len(control_field), self.n_steps)
		# self.make_additive()

	# Define multiplicative noise
	def make_multiplicative(self, multi_power = 1):
		"""
		Make the control field subject to multiplicative noise.  If the power spectrum 
		of the noise is proportional to the field value squared,
		
			>> object.make_multiplicative()
			
		If it proportional to the field value, 
		
			>> object.make_multiplicative(1./2)
			
		"""
		if self.control_defined == True:
			self.multi_power = multi_power
			self.is_multiplicative = True

	# Make additive noise
	def make_additive(self):
		"""
		Make the control field subject to additive noise. 
		
			>> object.make_additive()
			
		"""
		if self.control_defined == True:
			self.is_additive = True
			
	# Make constant offset noise	
	def make_constant(self, sigma=1.):
		"""
		Produce a zero-frequency noise term, of standard deviation, sigma
			
			>> sigma = 1.e3
			>> object.make_constant(sigma)
		"""
		self.noise_type = "constant"
		self.constant_sigma = sigma
		
	# Clear controls
	def clear_controls(self):
		"""
		Clear the defined control field.
		
			>> object.clear_controls()
			
		"""
		# self.control_defined = False
		self.is_additive = False
		self.is_multiplicative = False
		self.timing_error = False
		self.control_field = scipy.zeros(self.n_steps)
	
	def make_ou(self, sigma_in=1., decay_in=1.):
		"""
		Make Ornstein-Uhlenbeck noise with standard deviation sigma, and decay constant gamma
			
			>> object.make_ou(sigma, gamma)
		
		"""
		self.ou_sigma = sigma_in
		self.ou_decay = decay_in
		self.corrfn = map(lambda x: scipy.exp(-x**2/(2*sigma_in**2)), self.times)
		self.noise_type = 'ou'

	# Make white noise
	def make_white(self, magnitude=1.):
		"""
		Make white noise with power spectrum S(omega) = noise_power
		
			>> object.make_white(noise_power)
			
		"""
		
		self.corrfn = scipy.zeros(self.n_steps)
		self.sigma = sqrt(magnitude/self.dt)
		self.corrfn[0] = self.sigma
		self.noise_type = "white"
		
	# Make white and constant
	def make_white_and_constant(self, white_magnitude=1., constant_sigma=1.):
		"""
		Make white noise 
			>> object.make_white_and_constant(white_mag, const_mag)
		Where
			white_mag is the magnitude of the white noise S(omega) = white_mag
			const_mag is the magnitude of the constant noise 
				S(omega) = const_mag delta(omega)
		"""
		self.corrfn = scipy.ones(self.n_steps)*constant_sigma
		self.sigma = sqrt(white_magnitude/self.dt)
		self.corrfn[0] = self.sigma
		self.constant_sigma = constant_sigma
		self.noise_type = "white_and_constant"
		
	# Make user defined noise
	def make_defined(self, corrfn = lambda x: scipy.exp(-10.*x)):
		"""
		Make noise with a given correlation function
		
			>> corr_fn = lambda t: exp(-10.*x)
			>> object.make_defined(corr_fn)
			
		"""
		
		self.noise_type = "defined"
		self.corrfn = map(corrfn, scipy.linspace(0,self.t_final, self.n_steps))
		self.KL_calculated = False
		
	# Make Pink Noise with low and high frequency cutoffs
	def make_pink(self, amplitude = 1.):
		"""
		Make 1/f (pink) noise.  S(omega) = ampl / omega.  This method works 
		by defining: 
		  omega_min = 1/(10*t_final)
		  omega_max = 10*n_steps/t_final
		And integrating the 1/f power spectrum between these values, to get a correlation 
		function: 
		  C(t) = (ampl/pi)*[ sin(omega_min*t)/(omega_min*t) + ci(omega_max*t) - ci(omega_min*t) ]
		Where ci is the cosine integral function.
		
			>> object.make_pink(ampl)

		"""
		from scipy.special import sici
		from scipy import sin
		self.noise_type = "pink"
		omega_min = 1./(10*self.t_final)
		omega_max = 10*self.n_steps/self.t_final
		ci = lambda x: sici(x)[1]
		corrfn = lambda t: (amplitude/pi) * ((sin(omega_min*t)/(omega_min*t)) + ci(omega_max*t) - ci(omega_min*t))
		self.corrfn = map(corrfn, scipy.linspace(0,self.t_final, self.n_steps))
		self.corrfn[0]=self.corrfn[1]
		self.KL_calculated = False
		# pyplot.plot(self.corrfn)
		# pyplot.show()
		# self.calc_KL()
		# self.make_noise()
		# self.plot_noise()

	# Calculate the Karhunen-Loeve transform matrix
	def calc_KL(self):
		"""
		Compute the Karhunen-Loeve transformation matrix for the defined correlation function
		
			>> object.calc_KL()
			
		"""
		self.corrMat = toeplitz(self.corrfn)
		[self.evals, self.evecs] = eig(self.corrMat)
		self.KL_calculated = True
		print "KL transform calculated for", self.name

	# Contruct the colored noise from white noise using the KL transform
	def make_noise(self):
		"""
		Produce an instance of the noise based on the defined noise type.
		
			>> object.make_noise()
			
		"""
		if self.timing_error:
			self.times = self.input_times
			for index in range(1, len(self.times)-1):
				self.times[index] = self.times[index] + gauss(0,sqrt(self.timing_variance))
			# append(append([0],array([ x + gauss(0,sqrt(epsilon.timing_variance)) for x in epsilon.input_times[1:-1]])), epsilon.input_times[-1])
			# array([ x + gauss(0,sqrt(epsilon.timing_variance)) for x in epsilon.times[1:-1]])
			# self.times[0] = 0.  # XXXXXXXXXXX 
			# self.times[-1]  = self.input_times[-1]
			self.value = self.control_field
			if self.is_multiplicative or self.is_additive:
				print "Timing error noise cannot include other types of noise."
			return self.value
		
		if not (self.noise_type == 'white' and self.sigma == 0):
			xVec = scipy.zeros(self.n_steps)
			if self.noise_type == "constant":
				xVec = scipy.ones(self.n_steps)*gauss(0,self.constant_sigma)
			if self.noise_type == "white":
				for index in range(0,self.n_steps):
					xVec[index] = gauss(0,self.sigma)
			if self.noise_type == "ou":
				decayConstant = self.ou_decay
				sigma = self.ou_sigma
				mu = 0
				xVec = scipy.zeros([self.n_steps])
				xVec[0] = gauss(mu,sigma)
				scaleFactor = exp(-decayConstant*self.dt)
				for jj in range(0,self.n_steps-1):
					xVec[jj+1] = gauss(mu + scaleFactor*xVec[jj],sigma*sqrt(1-scaleFactor**2))
			if self.noise_type == "white_and_constant":
				for index in range(0,self.n_steps):
					xVec[index] = gauss(0,self.sigma)
				xVec = xVec + scipy.ones(self.n_steps)*gauss(0,self.constant_sigma)
			if (self.noise_type == "defined") or (self.noise_type=="pink"):
				if self.KL_calculated == False:
					self.calc_KL()
				zVec = scipy.zeros(self.n_steps)
				for index in xrange(0,self.n_steps):
					# zVec[index] = random.gauss(0,sqrt(self.evals[index]))
					zVec[index] = gauss(0,sqrt(abs(self.evals[index])))
				self.zVec = zVec
				xVec = dot(self.evecs,zVec)
			self.value = xVec
			
			# Multiply by control field for multiplicative noise
	 		if self.is_multiplicative == True:
				# print self.value
				self.value = (self.control_field**self.multi_power)*xVec
			
			# Add to control field for additive noise
			if self.is_additive == True:
				self.value = self.value + self.control_field
				
		else: self.value = self.control_field
		self.value = scipy.real(self.value)
		return self.value
	
	# Construct the sample cross correlation
	def xcov(self):
		"""
		Calculate the cross covariance matrix and the linear covariance for a
		particular noise instance.  
		
			>> object.xcov()
			>> pyplot.plot(object.linear_cov)
			
		"""
		from scipy import outer
		self.make_noise()
		self.xcov_matrix = outer(self.value,self.value)
		linear_cov = scipy.zeros(self.n_steps)
		for n in range(0, self.n_steps):
			linear_cov[n] = scipy.mean(scipy.diag(self.xcov_matrix,n))
		self.linear_cov = linear_cov
		return self.xcov_matrix
	
	def lcov(self, nruns = 1):
		"""
		Compute the average linear covariance
			>> linear_covariance = object.lcov()
			>> avg_linear_covariance = object.lcov(N)
		Where:
			N is the number of noise instances to average over. Defaults to 1
		"""
		# try:
		# 	self.linear_cov
		# except AttributeError:
		# 	self.xcov()
		# 	
		avg_linear_cov = scipy.zeros(self.n_steps)
		for n in range(0,nruns):
			self.xcov()
			avg_linear_cov = avg_linear_cov + self.linear_cov
		self.avg_linear_cov = avg_linear_cov/nruns
		return self.avg_linear_cov
	
		
	def plot_xcov(self, nruns = 1000):
		"""
		Plot the cross covariance matrix for N noise instances.  N defaults to 1000.
		
			>> object.plot_xcov()
			
		"""
		from mpl_toolkits.mplot3d import Axes3D
		
		q = scipy.zeros([self.n_steps,self.n_steps])
		p = scipy.zeros(self.n_steps)
		for index in range(0,nruns):
			q = q + self.xcov()
			p = p + self.linear_cov
		self.total_xcov = q/nruns
		self.total_lcov = p/nruns
		
		fig = pyplot.figure()
		ax = fig.gca(projection='3d')

		plot_step = 40
		rho = arange(0,plot_step)*scipy.floor(self.n_steps/plot_step)
		# rho = linspace(0,self.n_steps-1,self.n_steps)
		e1, e2 = scipy.meshgrid(rho,rho)
		ax.plot_surface(e1,e2,self.total_xcov[0:rho[-1]:plot_step,0:rho[-1]:plot_step], rstride=1, cstride=1, alpha=0.3, cmap=cm.jet, linewidth=0.1)
		pyplot.show()
		# if self.corrMat != 0:
		# 			ax.plot_surface(e1,e2,self.corrMat, rstride=32, cstride=32, alpha=0.3, cmap=cm.jet, linewidth=0.1)
		# 			pyplot.show()
		
	# Get the noise by index
	def get_noise(self, index):
		"""
		Return the noise value given the index of the noise
		
			>> index = 35
			>> object.get_noise(index)
			
		"""
		return self.value[index]
		
	def get_timed_noise(self, time):
		"""
		Get the noise value at a particular time, t. 
		
			>> t = 3.e-6
			>> noise_at_t = object.get_timed_noise(t)
			
		"""
		try: 
			index = abs(self.times - self.times[self.times<=time][-1]).argmin()
			if index >= len(self.value):
				return 0.
			else:
				return self.value[index]
		except:
			print "Cannot return noise."
			print "Error time is:", time
			print time == self.times[0]
			print "Name:", self.name

		
	def plot(self, n_plots = 1):
		"""
		Plot n instances of the noise (n defaults to 1)
		
			>> N = 4
			>> object.plot(N)

		"""
		for i in range(0,n_plots):
			self.make_noise()
			if len(self.value) == len(self.times)-1:
				pyplot.plot(self.times[0:len(self.times)-1],self.value)
			elif len(self.value) == len(self.times)-2:
				pyplot.plot(self.times[1:len(self.times)-1],self.value)
			else:	
				pyplot.plot(self.value)
		pyplot.show()
		
# Define Liouville class
class Liouvillian():
	"""
	The Liouvillian class contains the routines to propagate the system Hamiltonian - 
	including any relevant Lindblad operators - average those results over instances 
	of the noise terms, and return the process matrix or evolution superoperator.
	
	Requires:
		Field() class
		
	Optional Parameters:
		verbose = < True, False >
			Toggles verbosity
		very_verbose = < True, False >
			Toggles excessive verbosity
		hbar = <'one', 'eVs', 'Js' >
			Set hbar to 1., to its value in eV.s or its value in J.s depending on the inputs.
	
	Usage:
		Define the Hamiltonian as a function of all possible inputs:

			>> def hamiltonian(input_1, input_2, input_3):
					return input_1 * sigX + input_2 * (1 + input_3) * sigZ

		Create instances of the field class for each of the input parameters:

			>> n_steps = 1000
			>> t_final = 1.

		Define each of the 3 input parameters as instances of the Field class	

			>> input_1 = Field(t_final, n_steps)
			>> input_1.make_white(1.e-4)
			>> control_field = map(lambda x: sin(x), scipy.linspace(0,pi,n_steps))
			>> input_2 = Field(t_final, n_steps)
			>> input_2.define_control(control_field)
			>> input_3 = Field(t_final, n_steps)
			>> input_3.make_constant(1.e-6)

		Create an instance of the Liouiville class, passing the Hamiltonian
		definition, the input parameter objects, and any optional parameters,

			>> liouv = Liouiville(hamiltonian, 
				input_1, input_2, input_3, verbose=True, hbar = 'eVs')

		Define an amplitude damping process with T1 = 2.:

			>> liouv.set_T1(2.)

		Propagate the system for a single noise instance,

			>> liouv.propagate()

		or average over 1000 instances

			>> liouv.loop_propagate(1000)
			>> print lioui.get_process_matrix()

		or average over 1000 = 10*100 instances in parallel (in groups of 100)

			>> liouv.parallel_propagate(10)

		or run until the process matrices converge with 1.e-7 accuracy

			>> liouv.run_converging(1.e-7)

		Print the process matrix or the evolution superoperator

			>> print liouv.get_process_matrix()
			>> print liouv.get_evolution_superoperator()

	"""	
	# Define the Liouvillian constructor
	def __init__(self, hamiltonian_function, *input_fields, **keywords):
		""" Constructor"""
		self.verbose = False
		self.very_verbose = False
		self.fn_of_time = False
		self.timing_error = reduce(lambda x,y: x or y, [s.timing_error for s in input_fields])
		self.hbar = 1.
		self.gate_name = str(os.getpid())
		
		for k,v in keywords.iteritems():
			setattr(self,k,v)
		
		if self.hbar == 'one':
			self.hbar = 1.
		if self.hbar == 'eVs':
			self.hbar = 6.58211814e-16 # ev seconds
		if self.hbar == 'Js':
			self.hbar = 1.05457148e-34 # J seconds
		
		if self.very_verbose:
			self.verbose = True

		self.hamiltonian_function = hamiltonian_function
		self.input_fields = input_fields
	
		# If the n_qubits keyword is not defined, then define it based on the given Hamiltonian function
		try:
			self.n_qubits
		except AttributeError:
			samps = tuple(scipy.ones(len(input_fields)))
			self.n_qubits = int(log(len(hamiltonian_function(*samps)))/log(2.))

		self.n_steps = input_fields[0].n_steps
		self.t_final = input_fields[0].t_final
		self.dt = 1.*self.t_final/self.n_steps

		if self.verbose:
			print "Louivillian defined on %d qubits." % self.n_qubits
			print "The final time is %f." % self.t_final
			print "Results will be calculated using %d time steps." % self.n_steps
		

		# self.hamiltonian_matrices = scipy.zeros([self.n_steps, 2**self.n_qubits, 2**self.n_qubits])
		
		self.use_lindblad = False
		self.lindblad_superoperator = scipy.zeros([4**self.n_qubits,4**self.n_qubits])

		self.sigI = mat([[1,0],[0,1]])

		# The process matrix basis is normalized so that Tr(p_i,p_j) = delta_i,j
		partial_basis = [sigI/2, sigX/2, sigY/2, sigZ/2]
		if self.n_qubits == 1:
			self.identity = self.sigI
			self.process_basis = partial_basis
		if self.n_qubits > 1:
			self.identity = reduce(kron,(sigI,)*self.n_qubits)
			self.process_basis = reduce(kron,(partial_basis,)*self.n_qubits)

	def set_name(self, gate_name):
		"""
		Give a name to the stochatic process.
			>> object.set_name('name')
		"""
		self.gate_name = gate_name

	# Generate new instances of the stochastic processes
	def reinitialize_stochastics(self):
		"""
		Calculate new instances of the stochastic processes.
			>> object.reinitialize_stochastics()
		"""
		[ x.make_noise() for x in self.input_fields ]
	
	def explicit_function_of_time(self):
		"""
		Set whether the Hamiltonian is a function of time
			>> object.explicit_function_of_time()
		"""
		self.fn_of_time = True

	def not_explicit_function_of_time(self):
		"""
		Set whether the Hamiltonian is a function of time
			>> object.not_explicit_function_of_time()
		"""
		self.fn_of_time = False

	
	# For a given instance of the noise processes, construct the hamiltonian sequence
	def build_hamiltonians(self):
		"""
		Reinitialize the stochastic processes and explicitly define the 
		Hamiltonian matrices as a function of time.
			>> object.build_hamiltonians()
		"""
		self.reinitialize_stochastics() 
		self.times = unique(reduce(append, [x.times for x in self.input_fields]))
		self.dts = diff(self.times)
		if self.very_verbose:
			print [x.get_timed_noise(self.times[0]) for x in self.input_fields]
		if self.fn_of_time:
			self.hamiltonian_matrices =  [self.hamiltonian_function(time, *tuple([x.get_timed_noise(time) for x in self.input_fields])) for time in self.times]
		if not(self.fn_of_time):
			self.hamiltonian_matrices =  [self.hamiltonian_function(*tuple([x.get_timed_noise(time) for x in self.input_fields])) for time in self.times]

	# Set a T1 time for the system
	def set_T1(self, t1 = 1.):
		"""
		In a system that exhibits amplitude damping, define the T1 time:
			>> t1 = 3.e-5
			>> object.set_T1(t1)
		Where:
			t1 is the T1 time, defaults to 1. if not specified
		"""
		sigma_minus = mat([[0,0],[1.,0]])
		scale_factor = 1./(t1)
		if self.n_qubits == 1:
			lindblad_operator = sigma_minus
			self.add_lindblad(lindblad_operator, scale_factor)
		if self.n_qubits == 2:
			lindblad_operator1 = kron(sigI, sigma_minus)
			lindblad_operator2 = kron(sigma_minus, sigI)
			self.add_lindblad(lindblad_operator1, scale_factor)
			self.add_lindblad(lindblad_operator2, scale_factor)

	# Set a T2 time for the system
	def set_T2(self, t2 = 1.):
		"""
		In a system that exhibits amplitude damping, define the T2 time:
			>> t2 = 3.e-5
			>> object.set_T2(t2)
		Where:
			t2 is the T2 time, defaults to 1. if not specified
		"""
		scale_factor = 1./(t2)
		if self.n_qubits == 1:
			lindblad_operator = sigZ
			self.add_lindblad(lindblad_operator, scale_factor)
		if self.n_qubits == 2:
			lindblad_operator1 = kron(sigI, sigZ)
			lindblad_operator2 = kron(sigZ, sigI)
			self.add_lindblad(lindblad_operator1, scale_factor)
			self.add_lindblad(lindblad_operator2, scale_factor)

	# Add a lindblad evolution operator to the system.
	def add_lindblad(self, lindblad_operator, scale_factor = 1.):
		"""
		In a system undergoing Lindblad evolution, set the Lindblad operator:
			>> L_1 = mat([[0,0],[1,0]])
			>> scale_factor = 2.
			>> object.add_lindblad(L_1)
			>> object.add_lindblad(L_1, scale_factor)
		Where:
			L_1 is the Lindblad operator
			scale_factor is the Lindblad rate
				d rho / dt = scale_factor * ( L_1 * rho * L_1.H - L_1.H * L_1 * rho / 2 - rho * L_1.H * L_1 / 2)
		"""
		self.use_lindblad = True
		lindblad_operator = mat(lindblad_operator)
		if shape(lindblad_operator) == (2**self.n_qubits,2**self.n_qubits):
			lindblad_superoperator_TEMP = kron(lindblad_operator.H.T, lindblad_operator) - 0.5 * kron(self.identity, dot(lindblad_operator.H, lindblad_operator)) - 0.5 * kron(dot(lindblad_operator.H, lindblad_operator).T, self.identity)
			if self.verbose:
				print "Added Lindblad superoperator:"
				print scale_factor*lindblad_superoperator_TEMP
			self.lindblad_superoperator = self.lindblad_superoperator + scale_factor*lindblad_superoperator_TEMP
		else:
			print "lindblad_operator is the wrong dimension and will not be included in evolution"
	
	# Calculate the evolution superoperator for a given sample run 
	def propagate(self, null=0, return_unitary = False):
		"""
		Propagate the system under the defined evolution operator.
			>> object.propagate()
			>> object.propagate(null)
		Propagate the system and return the unitary matrix, not the superoperator.
			>> object.propagate(null, True)
		Where:
			null 
				a null arguement usefull for using propagate() in a map statement
		"""
		self.build_hamiltonians()
		if self.use_lindblad == True: 
			superoperator_matrix = mat(kron(self.identity,self.identity))
			for index in range(0, len(self.dts)):
				intermediate_differential_operator = -1.j * ( kron(self.identity, self.hamiltonian_matrices[index]) - kron(self.hamiltonian_matrices[index].T, self.identity)) / self.hbar + self.lindblad_superoperator
				intermediate_superoperator_matrix = mat(linalg.expm(intermediate_differential_operator * self.dts[index]))
				superoperator_matrix = intermediate_superoperator_matrix*superoperator_matrix
			self.set_evolution_superoperator(superoperator_matrix)
			if self.verbose:
				print "Evolution superoperator:"
				print round_(superoperator_matrix,3)
			return superoperator_matrix
		if self.use_lindblad == False:
			unitary_matrix = self.identity
			for index in range(0, len(self.dts)):
				try:
					intermediate_unitary_matrix = mat(linalg.expm( -1.j * self.hamiltonian_matrices[index] * self.dts[index] / self.hbar))
					unitary_matrix = intermediate_unitary_matrix*unitary_matrix
				except IndexError:
					print "IndexError"
					print "index:", index
			# Return the evolution superoperator for a single noise trace
			if self.very_verbose:
				print "Final Hamiltonian"
				print self.hamiltonian_matrices[self.n_steps-1]
			if self.verbose:
				print "Final unitary matrix"
				print round_(unitary_matrix,3)
			self.unitary_matrix = unitary_matrix
			superoperator_matrix = kron(unitary_matrix.H, unitary_matrix)
			self.set_evolution_superoperator(superoperator_matrix)
			if self.verbose:
				print "Evolution superoperator:"
				print round_(superoperator_matrix,3)
				print "Process matrix:"
				print round_(self.convert_super_to_process(superoperator_matrix),3)

			if return_unitary:
				return unitary_matrix
			else:
				return superoperator_matrix
	
	# Write process matrix to file
	def write_process_matrix(self, tolerance=1.e-8, *input_process_matrix):
		"""
		Write the process_matrix to the file
			./process_matrix_[gate_name].out
		Call as
			>> object.write_process_matrix()
			>> object.write_process_matrix(tolerance)
			>> object.write_process_matrix(tolerace, input_process_matrix)
		If no input_process_matrix is given, this will write the stored process 
		matrix to the file, rounded to the nearest (1.e-(8+1)).  If tolerance 
		is specified, then the process matrix is rounded to approximately
		the nearest (tolerance/10).
		"""
		import os
		# import pickle
		output_file_name = './process_matrix_'+self.gate_name+'.out'
		out_file = open(output_file_name, 'w')
		# if len(input_process_matrix) == 1:
		# 			pickle.dump(input_process_matrix, out_file)
		# 		else:
		# 			pickle.dump(self.process_matrix, out_file)
		precision_value = int(abs(scipy.ceil(log(tolerance)/log(10.)))+1)
		q = round_(self.process_matrix, precision_value)
		a1 = self.gate_name + '\n\n'+str(self.t_final) + '\n\n[\n\t['
		for x in q:
			for y in x:
				# if y==scipy.real(y):
				# 					a1 = a1 + str(y) + ', '
				# 				elif y==0.:
				a1 = a1 + '(' + str(scipy.real(y)) + ',' + str(scipy.imag(y)) + '), '
			a1 = a1[0:-2]+'],\n\t['
		a1 = a1[0:-4]+'\n]\n\n'
		out_file.write(a1)
		out_file.close()
		
	
	# Write process matrix to file
	def write_evolution_superoperator(self, *input_evolution_matrix):
		"""
		Write the evolution superoperator to the file
			./evolution_superoperator_[process_ID].out
		Call as
			>> object.write_evolution_superoperator()
			>> object.write_evolution_superoperator(input_evolution_matrix)
		If no input is given, this will write the stored evolution matrix to the file.
		"""
		import os
		import pickle
		# File name is './out_file_PID.out'
		output_file_name = './process_matrix_'+self.gate_name+'.out'
		out_file = open(output_file_name, 'w')
		if len(input_evolution_matrix) == 1:
			pickle.dump(input_evolution_matrix, out_file)
		else:
			pickle.dump(self.evolution_superoperator, out_file)
		out_file.close()

	def convert_super_to_process(self, superoperator):
		process_matrix = scipy.zeros([4**self.n_qubits, 4**self.n_qubits],'complex')
		for index1 in range(0, 4**self.n_qubits):
			for index2 in range(0, 4**self.n_qubits):
				# print index1,index2
				s = mat(kron(self.process_basis[index2].T,self.process_basis[index1]))*mat(superoperator)
				process_matrix[index1,index2] = (s.trace()[0,0])
		return process_matrix

	# Store the evolution superoperator
	def set_evolution_superoperator(self, superoperator_value):
		"""
		Define the attribute object.evolution_superoperator
			>> object.set_evolution_superoperator(superoperator_value)
		Where 
			superoperator_value is evolution superoperator calculated by propagate()
		"""
		self.evolution_superoperator = superoperator_value
		self.process_matrix = self.convert_super_to_process(superoperator_value)
	 	if self.very_verbose:
			print "Single step superoperator"
			print superoperator_value
		if self.very_verbose:
			print "Single step process matrix"
			print process_matrix

		# process_matrix = scipy.zeros([4**self.n_qubits, 4**self.n_qubits],'complex')
		# for index1 in range(0, 4**self.n_qubits):
		# 	for index2 in range(0, 4**self.n_qubits):
		# 		# print index1,index2
		# 		s = mat(kron(self.process_basis[index2].T,self.process_basis[index1]))*mat(superoperator_value)
		# 		process_matrix[index1,index2] = (s.trace()[0,0])
		# 		# print s.trace()[0,0]


	# Define the process matrix
	def set_process_matrix(self, process_matrix_value):
		self.process_matrix = process_matrix_value

	# Read out the evolution superoperator
	def get_process_matrix(self):
		"""
		Return the process_matrix:
			>> print object.get_process_matrix()
		"""
		return self.process_matrix

	# Read out the evolution superoperator
	def get_evolution_superoperator(self):
		"""
		Return the evolution superoperator:
			>> print object.get_evolution_superoperator()
		"""
		return self.evolution_superoperator

	# Evolve a loop
	@timeit
	def loop_propagate(self, n_reps=100, write = False):
		"""
		Propagate the system n_reps times and average the resulting 
		process matrices and evolutions superoperators
			>> n_reps = 1000
			>> object.loop_propagate(n_reps)
		Where:
			n_reps
				The number of iterations to take.  Defaults to 100.
		"""
		superoperator_out = sum(map(self.propagate, range(0,n_reps)),0)/n_reps
		self.set_evolution_superoperator(superoperator_out)
		if write:
			self.write_process_matrix(self.get_process_matrix())
		if self.verbose:
			print "Final evolution superoperator after %d repetitions:" % n_reps
			print superoperator_out
			print "Final process matrix after %d repetitions:" % n_reps
			print self.get_process_matrix()
		return superoperator_out
			
	@timeit	
	def run_converging(self, tolerance = 1.e-6, use_parallel = False, n_below_tols = 1, n_iterations = 100, verbose_convergence = True):
		self.reinitialize_stochastics()
		if use_parallel:
			# running_process_matrix = self.parallel_propagate(1, n_iterations)
			running_evolution_superoperator = self.parallel_propagate(1, n_iterations)
		else:
			running_evolution_superoperator = self.loop_propagate(n_iterations)
		completed_iterations = 1
		times_below_tolerance = 0
		error = 1.
		
		if verbose_convergence:
			print ""
			print "completed:", completed_iterations
			print ""
			print "running process matrix of %d steps" % n_iterations
			print running_evolution_superoperator
			print "process matrix of %d steps" % n_iterations
			print running_evolution_superoperator
		
		while times_below_tolerance < n_below_tols:
			if use_parallel:
				current_evolution_superoperator = self.parallel_propagate(1, n_iterations)
			else:
				current_evolution_superoperator = self.loop_propagate(n_iterations)
			if scipy.isnan(current_evolution_superoperator).any():
				continue
			average_evolution_superoperator = running_evolution_superoperator
			running_evolution_superoperator = (completed_iterations*running_evolution_superoperator + current_evolution_superoperator) / (completed_iterations+1)
			completed_iterations = completed_iterations + 1
			error = abs(running_evolution_superoperator - average_evolution_superoperator).max()
			if error < tolerance:
				times_below_tolerance = times_below_tolerance + 1
			else:
				times_below_tolerance = 0
			if verbose_convergence:
				print ""
				print "completed:", completed_iterations
				print ""
				print "running evolution superoperator of %d steps" % (n_iterations*completed_iterations)
				print running_evolution_superoperator
				print "running evolution superoperator of %d steps" % (n_iterations*(completed_iterations-1))
				print average_evolution_superoperator
				print "evolution superoperator of %d steps" % n_iterations
				print current_evolution_superoperator
				print "error after %d steps is %1.10f" % (completed_iterations, error)
			self.running_evolution_superoperator = running_evolution_superoperator
		self.set_evolution_superoperator(running_evolution_superoperator)
		return running_evolution_superoperator

    # Run Monte Carlo routine to get the evolution superoperator averaged over noise instances
	@timeit # Add timing decorator to parallel propagater
	def parallel_propagate(self, n_bigs = 1, n_reps = 100):
		"""
		Propagate the system (n_reps * n_bigs) times and average the resulting 
		process matrices and evolutions superoperators.  This requires a shared-memory
		multi-core machine and the 'multiprocessing' python library
			>> n_bigs = 100
			>> n_reps = 50
			>> object.parallel_evolve(n_bigs)
			>> object.parallel_evolve(n_bigs, n_reps)
		Where:
			n_bigs
				The number of times to loop over n_reps iterations
			n_reps 
				Due to limitations of the parallel map routine, n_reps
				appears limited to about 127.  If not included, it defaults 
				to 100.
		"""
		# if self.verbose:
		# 	print __name__
		# if __name__ == 'processMC':  # THIS LINE MIGHT BE REQUIRED ON A WINDOWS MACHINE
		for i in range(0, n_bigs):
			temp_out = sum(parmap(self.propagate,range(0,n_reps)),0)/n_reps
			if i == 0:
				out_val = temp_out
			else:
				out_val = out_val + temp_out
		evolution_superoperator = out_val/n_bigs
		self.set_evolution_superoperator( evolution_superoperator )
		process_out = self.get_process_matrix()
		return evolution_superoperator
