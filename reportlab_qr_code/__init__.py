# -*- coding: utf-8 -*-
import array
import operator
import re
import math
from base64 import b64decode
from dataclasses import dataclass

import qrcode
from reportlab.lib.units import toLength
from reportlab.pdfgen.canvas import FILL_EVEN_ODD


DEFAULT_PARAMS = {
	'version': None,
	'error_correction': 'L',
}
FALSE_VALUES = {'off', 'false', 'False', '0', False, 0, None}
QR_PARAMS = {'version', 'error_correction'}
QR_ERROR_CORRECTIONS = {
	'L': qrcode.ERROR_CORRECT_L,
	'M': qrcode.ERROR_CORRECT_M,
	'Q': qrcode.ERROR_CORRECT_Q,
	'H': qrcode.ERROR_CORRECT_H,
}
DIRECTION = (
	( 1,  0), # right
	( 0,  1), # down
	(-1,  0), # left
	( 0, -1), # up
)
DIRECTION_TURNS_CHECKS = (
	( 0,  0), # right
	(-1,  0), # down
	(-1, -1), # left
	( 0, -1), # up
)

LENGTH_ABSOLUTE = 0
LENGTH_PIXELS = 1
LENGTH_RELATIVE = 2


@dataclass
class Length(object):
	__slots__ = ['kind', 'value']

	kind: int
	value: int


class Vector(tuple):
	def __add__(self, other):
		return self.__class__(map(operator.add, self, other))


class Transforms:
	@staticmethod
	def to_length(val):
		return toLength(val) if isinstance(val, str) else float(val)

	@staticmethod
	def to_bool(val):
		return val not in FALSE_VALUES

	@staticmethod
	def to_float(val):
		return float(val)

	@staticmethod
	def to_area(val):
		parts = val.split(':')
		if len(parts) % 4 != 0:
			raise ValueError(f"Wrong value `{val}`, expected coordinates: x:y:w:h")
		coordinates = []
		for coordinate in parts:
			if coordinate[-1:] == '%':
				coordinates.append(Length(LENGTH_RELATIVE, float(coordinate[:-1]) / 100))
			else:
				try:
					coordinates.append(Length(LENGTH_PIXELS, int(coordinate)))
				except ValueError:
					coordinates.append(Length(LENGTH_ABSOLUTE, Transforms.to_length(coordinate)))
		coordinates = [coordinates[i:i + 4] for i in range(0, len(coordinates), 4)]
		for area in coordinates:
			if not all(coordinate.kind == area[0].kind for coordinate in area):
				raise ValueError(f"Mixed units in `{val}`")
		return coordinates


class ReportlabImageBase(qrcode.image.base.BaseImage):
	PARAMS = {
		'size': Transforms.to_length,
		'padding': None,
		'fg': None,
		'bg': None,
		'x': Transforms.to_length,
		'y': Transforms.to_length,
		'invert': Transforms.to_bool,
		'negative': Transforms.to_bool,
		'mask': Transforms.to_bool,
		'radius': Transforms.to_float,
		'enhanced_path': Transforms.to_bool,
		'hole': Transforms.to_area,
	}

	size = toLength('5cm')
	padding = '2.5'
	bg = None
	fg = '#000000'
	bg_alpha = 1.0
	fg_alpha = 1.0
	bitmap = None
	x = 0
	y = 0
	invert = False
	negative = False
	mask = False
	enhanced_path = None
	hole = []
	radius = 0
	draw_parts = None

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.bitmap = array.array('B', [1 if self.invert else 0] * self.width * self.width)
		if isinstance(self.padding, str) and '%' in self.padding:
			self.padding = float(self.padding[:-1]) * self.size / 100
		else:
			try:
				self.padding = float(self.padding)
				self.padding = (self.size / (self.width + self.padding * 2)) * self.padding
			except ValueError:
				self.padding = toLength(self.padding) if isinstance(self.padding, str) else float(self.padding)
		if self.enhanced_path is None:
			self.enhanced_path = self.radius == 0
		self.parse_color('fg')
		self.parse_color('bg')
		self.hole = [self.convert_area_to_pixels(fragment) for fragment in self.hole]

	def drawrect(self, row, col):
		self.bitmap_set((col, row), 0 if self.invert else 1)

	def save(self, stream, kind=None):
		if not self.mask:
			stream.saveState()

		a0, b0, c0, d0, e0, f0 = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0) # current matrix

		self.clear_area()

		def transform(a, b, c, d, e, f):
			nonlocal a0, b0, c0, d0, e0, f0
			matrix = (a0*a+c0*b, b0*a+d0*b, a0*c+c0*d, b0*c+d0*d, a0*e+c0*f+e0, b0*e+d0*f+f0)
			a0, b0, c0, d0, e0, f0 = matrix
			stream.transform(a, b, c, d, e, f)

		def restore_transform():
			nonlocal a0, b0, c0, d0, e0, f0
			det = a0*d0 - c0*b0
			a = d0/det
			c = -c0/det
			e = (c0*f0 - d0*e0)/det
			d = a0/det
			b = -b0/det
			f = (e0*b0 - f0*a0)/det
			stream.transform(a, b, c, d, e, f)

		try:
			# Move to start
			transform(1.0, 0.0, 0.0, -1.0, self.x, self.y + self.size)

			if not self.mask:
				self.draw_background(stream)
				# Set foreground
				stream.setFillColor(self.fg)
				stream.setFillAlpha(self.fg_alpha)

			# Set transform matrix
			scale = (self.size - (self.padding * 2.0)) / self.width
			transform(scale, 0.0, 0.0, scale, self.padding, self.padding)

			p = stream.beginPath()
			if self.negative:
				pad = self.padding / scale
				p.moveTo(-pad, -pad)
				p.lineTo(self.width + pad, -pad)
				p.lineTo(self.width + pad, self.width + pad)
				p.lineTo(-pad, self.width + pad)
				p.close()
			if self.radius == 0:
				p = self.draw_code(p)
			else:
				p = self.draw_rounded_code(p)
			if self.mask:
				stream.clipPath(p, stroke=0, fill=1, fillMode=FILL_EVEN_ODD)
			else:
				stream.drawPath(p, stroke=0, fill=1, fillMode=FILL_EVEN_ODD)
		finally:
			if self.mask:
				restore_transform()
				stream.setFillAlpha(1.0)
			else:
				stream.restoreState()

	def draw_background(self, stream):
		"""
		Draw rectangle on background if is not transparent
		"""
		if self.bg is not None:
			stream.setFillColor(self.bg)
			stream.setFillAlpha(self.bg_alpha)
			stream.rect(0, 0, self.size, self.size, fill=1, stroke=0)

	def draw_code(self, p):
		"""
		Draw QR code
		"""
		for segment in self.get_segments():
			p.moveTo(segment[0][0], segment[0][1])
			for coords in segment[1:-1]:
				p.lineTo(coords[0], coords[1])
			p.close()
		return p

	def draw_rounded_code(self, p):
		"""
		Draw QR code using rounded paths
		"""
		for segment in self.get_segments():
			segment = segment[:-1]
			for i in range(0, len(segment)):
				coords = segment[i]
				prev_coords = segment[i - 1]
				next_coords = segment[(i + 1) % len(segment)]
				prev_dir = self.__calc_round_direction(prev_coords, coords, self.radius)
				next_dir = self.__calc_round_direction(next_coords, coords, self.radius)
				if i == 0:
					p.moveTo(coords[0] + prev_dir[0], coords[1] + prev_dir[1])
				else:
					p.lineTo(coords[0] + prev_dir[0], coords[1] + prev_dir[1])
				c = 0.45 # 1 - (4/3)*tan(pi/8)
				p.curveTo(
					coords[0] + prev_dir[0] * c, coords[1] + prev_dir[1] * c,
					coords[0] + next_dir[0] * c, coords[1] + next_dir[1] * c,
					coords[0] + next_dir[0], coords[1] + next_dir[1],
				)
			p.close()
		return p

	def addr(self, coords):
		"""
		Get index to bitmap
		"""
		col, row = coords
		if row < 0 or col < 0 or row >= self.width or col >= self.width:
			return None
		return row * self.width + col

	def coord(self, addr):
		"""
		Returns bitmap coordinates from address
		"""
		return Vector((addr % self.width, addr // self.width))

	def bitmap_get(self, coords):
		"""
		Returns pixel value of bitmap
		"""
		addr = self.addr(coords)
		return 0 if addr is None else self.bitmap[addr]

	def bitmap_set(self, coords, value):
		"""
		Set pixel value of bitmap
		"""
		addr = self.addr(coords)
		self.bitmap[addr] = value

	def bitmap_invert(self, coords):
		"""
		Invert value of pixel
		"""
		addr = self.addr(coords)
		self.bitmap[addr] = 0 if self.bitmap[addr] else 1

	def get_segments(self):
		"""
		Return list of segments (vector shapes)
		"""
		segments = []
		segment = self.__consume_segment()
		while segment:
			segments.append(segment)
			segment = self.__consume_segment()
		return segments

	def parse_color(self, color_name):
		color = getattr(self, color_name)
		if color is None:
			return
		alpha = 1.0
		if re.match(r'^#[0-9a-f]{8}$', color.lower()):
			color = color.lower()
			alpha = int(color[-2:], base=16) / 255
			color = color[:7]
		setattr(self, color_name, color)
		setattr(self, f'{color_name}_alpha', alpha)

	def convert_absolute_to_relative(self, coordinate):
		if coordinate.kind == LENGTH_ABSOLUTE:
			return Length(LENGTH_RELATIVE, (coordinate.value - self.padding) / (self.size - self.padding * 2))
		return coordinate

	def convert_coordinate_to_pixels(self, coordinate, round_up=False):
		if coordinate.kind == LENGTH_PIXELS:
			return coordinate.value
		else:
			value = coordinate.value * self.width
			return math.ceil(value) if round_up else math.floor(value)

	def convert_area_to_pixels(self, area):
		area = [self.convert_absolute_to_relative(coordinate) for coordinate in area]
		x, y, w, h = area
		x_px = max(self.convert_coordinate_to_pixels(x), 0)
		y_px = max(self.convert_coordinate_to_pixels(y), 0)
		x2 = Length(x.kind, x.value + w.value)
		y2 = Length(y.kind, y.value + h.value)
		w_px = max(min(self.convert_coordinate_to_pixels(x2) - x_px, self.width - x_px), 1)
		h_px = max(min(self.convert_coordinate_to_pixels(y2) - y_px, self.width - y_px), 1)
		return [x_px, y_px, w_px, h_px]

	def clear_area(self):
		for hole in self.hole:
			for x in range(hole[0], hole[0] + hole[2]):
				for y in range(hole[1], hole[1] + hole[3]):
					self.bitmap_set((x, y), 0)

	def __consume_segment(self):
		"""
		Returns segment of qr image as path (pairs of x, y coordinates)
		"""

		line_intersections = [[] for __ in range(self.width)]

		try:
			# Find first pixel
			coords = self.coord(self.bitmap.index(1))
		except ValueError:
			# Or no pixels left
			return

		def move():
			nonlocal coords
			step = DIRECTION[direction]

			# Record intersection
			if step[1]: # Vertical move
				line = coords[1]
				if step[1] == -1:
					line -= 1
				line_intersections[line].append(coords[0])

			# Step
			coords += step

		# Accumulated path
		path = []
		# Begin of line
		path.append(tuple(coords))
		# Default direction to right
		direction = 0
		# Default clockwiese direction
		clockwiese = 1

		# Move to right
		move()

		# From shape begin to end
		while coords != path[0]:
			# Trun left
			val = self.bitmap_get(coords + DIRECTION_TURNS_CHECKS[(direction - max(0, clockwiese)) % 4])
			if val:
				if self.enhanced_path:
					# Detect intersection pattern and change direction
					if not self.bitmap_get(coords + DIRECTION_TURNS_CHECKS[(direction + min(0, clockwiese)) % 4]):
						move()
						clockwiese = -clockwiese
						continue;
				path.append(tuple(coords))
				direction = (direction - clockwiese) % 4
				move()
				continue

			# Straight
			val = self.bitmap_get(coords + DIRECTION_TURNS_CHECKS[(direction + min(0, clockwiese)) % 4])
			if val:
				move()
				continue

			# Trun right
			path.append(tuple(coords))
			direction = (direction + clockwiese) % 4
			move()

		path.append(tuple(coords))

		# Remove shape from bitmap
		for row, line in enumerate(line_intersections):
			line = sorted(line)
			for start, end in zip(line[::2], line[1::2]):
				for col in range(start, end):
					self.bitmap_invert((col, row))

		return path

	def __calc_round_direction(self, src, dst, radius):
		return [min(max((s - d) * 0.5, -radius), radius) for s, d in zip(src, dst)]


def transform_part_params(params, kwargs, base=ReportlabImageBase):
	# Chceck each parameter
	for key, value in kwargs.items():
		# If is unknown, raise exception
		if key not in base.PARAMS:
			raise ValueError("Unknown attribute '%s'" % key)
		# Get transform function like to int
		transform = base.PARAMS[key]
		try:
			params[key] = value if transform is None else transform(value)
		except Exception as e:
			raise ValueError("Wrong value '%s' for attribute %s: %s" % (value, key, e))


def reportlab_image_factory(base=ReportlabImageBase, **kwargs):
	"""
	Returns ReportlabImage class for qrcode image_factory
	"""

	# specific part settings
	draw_parts = kwargs.pop('draw_parts', [])

	# transform common parameters
	params = {}
	transform_part_params(params, kwargs, base)

	parts = []
	# parse parameters specific for pparts
	for part in draw_parts:
		draw_command = part.pop('draw')
		part_params = {}
		transform_part_params(part_params, part, base)
		part_params['draw'] = draw_command
		parts.append(part_params)

	if parts:
		params['draw_parts'] = parts

	return type('ReportlabImage', (base,), params)


def clean_params(params):
	"""
	Validate and clean parameters
	"""

	if params['version'] is not None:
		try:
			params['version'] = int(params['version'])
		except ValueError:
			raise ValueError("Version '%s' is not a number" % params['version'])

	if params['error_correction'] in QR_ERROR_CORRECTIONS:
		params['error_correction'] = QR_ERROR_CORRECTIONS[params['error_correction']]
	else:
		raise ValueError("Unknown error correction '%s', expected one of %s" % (params['error_correction'], ', '.join(QR_ERROR_CORRECTIONS.keys())))


def parse_params_string(params):
	"""
	Parses params string in form:

	key=value,key2=value2;(text|base64);content

	For example:

	size=5cm,fg=#ff0000,bg=#ffffff,version=1,error_correction=M,padding=5%;text;text to encode
	"""
	try:
		parsed_params, fmt, text = params.split(';', 2)
	except ValueError:
		raise ValueError("Wrong format, expected parametrs;format;content")
	if fmt not in ('text', 'base64'):
		raise ValueError("Unknown format '%s', supprted are text or base64" % fmt)

	params = DEFAULT_PARAMS.copy()

	# allow draw specific parts with different settings
	params['draw_parts'] = []
	curreent_draw = params

	if parsed_params:
		try:
			for item in parsed_params.split(','):
				key, value = item.split('=', 1)
				if key == 'draw':
					curreent_draw = {'draw': value}
					params['draw_parts'].append(curreent_draw)
				else:
					curreent_draw[key] = value
		except ValueError:
			raise ValueError("Wrong format of parameters '%s', expected key=value pairs delimited by ',' character" % parsed_params)

	clean_params(params)

	text = text.encode('utf-8')
	if fmt == 'base64':
		try:
			text = b64decode(text)
		except Exception as e:
			raise ValueError("Wrong base64 '%s': %s" % (text.decode('utf-8'), e))

	return params, text


def build_qrcode(params, text):
	factory_kwargs = {key: value for key, value in params.items() if key not in QR_PARAMS}
	qr_kwargs = {key: value for key, value in params.items() if key in QR_PARAMS}
	qr = qrcode.QRCode(image_factory=reportlab_image_factory(**factory_kwargs), border=0, **qr_kwargs)
	qr.add_data(text)
	return qr.make_image()


def qr_draw(canvas, text, **kwargs):
	params = DEFAULT_PARAMS.copy()
	params.update(**kwargs)
	clean_params(params)
	if isinstance(text, str):
		text = text.encode('utf-8')
	build_qrcode(params, text).save(canvas)


def qr(canvas, params=None):
	"""
	Generate QR code using plugInGraphic or plugInFlowable

	Example RML code:

	<illustration height="5cm" width="5cm" align="center">
		<plugInGraphic module="reportlab_qrcode" function="qr">size=5cm;text;Simple text</plugInGraphic>
	</illustration>
	"""
	build_qrcode(*parse_params_string(params)).save(canvas)
