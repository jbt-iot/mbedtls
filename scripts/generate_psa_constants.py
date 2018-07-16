#!/usr/bin/env python
import os
import re
import sys

output_template = '''\
/* Automatically generated by generate_psa_constant.py. DO NOT EDIT. */

static const char *psa_strerror(psa_status_t status)
{
    switch (status) {
    %(status_cases)s
    default: return NULL;
    }
}

static const char *psa_ecc_curve_name(psa_ecc_curve_t curve)
{
    switch (curve) {
    %(ecc_curve_cases)s
    default: return NULL;
    }
}

static const char *psa_hash_algorithm_name(psa_algorithm_t hash_alg)
{
    switch (hash_alg) {
    %(hash_algorithm_cases)s
    default: return NULL;
    }
}

static int psa_snprint_key_type(char *buffer, size_t buffer_size,
                                psa_key_type_t type)
{
    size_t required_size = 0;
    switch (type) {
    %(key_type_cases)s
    default:
        %(key_type_code)s{
            return snprintf(buffer, buffer_size,
                            "0x%%08lx", (unsigned long) type);
        }
        break;
    }
    buffer[0] = 0;
    return required_size;
}

static void append_padding_mode(char **buffer, size_t buffer_size,
                                size_t *required_size,
                                psa_algorithm_t padding_mode)
{
    size_t n;
    append(buffer, buffer_size, required_size, " | ", 3);
    switch (padding_mode) {
    %(padding_mode_cases)s
    default:
        n = snprintf(*buffer, buffer_size - *required_size,
                     "0x%%08lx", (unsigned long) padding_mode);
        if (n < buffer_size - *required_size) *buffer += n;
        *required_size += n;
        break;
    }
}

static int psa_snprint_algorithm(char *buffer, size_t buffer_size,
                                 psa_algorithm_t alg)
{
    size_t required_size = 0;
    psa_algorithm_t padding_mode = -1;
    psa_algorithm_t alg_without_padding = alg;
    if (PSA_ALG_IS_CIPHER(alg) && PSA_ALG_IS_BLOCK_CIPHER(alg)) {
            padding_mode = alg & PSA_ALG_BLOCK_CIPHER_PADDING_MASK;
            alg_without_padding = alg & ~PSA_ALG_BLOCK_CIPHER_PADDING_MASK;
    }
    switch (alg_without_padding) {
    %(algorithm_cases)s
    default:
        %(algorithm_code)s{
            return snprintf(buffer, buffer_size,
                            "0x%%08lx", (unsigned long) alg);
        }
        break;
    }
    if (padding_mode != (psa_algorithm_t) -1) {
        append_padding_mode(&buffer, buffer_size, &required_size, padding_mode);
    }
    buffer[0] = 0;
    return required_size;
}

static int psa_snprint_key_usage(char *buffer, size_t buffer_size,
                                 psa_key_usage_t usage)
{
    size_t required_size = 0;
    if (usage == 0) {
        if (buffer_size > 1) {
            buffer[0] = '0';
            buffer[1] = 0;
        } else if (buffer_size == 1) {
            buffer[0] = 0;
        }
        return 1;
    }
%(key_usage_code)s
    if (usage != 0) {
        if (required_size != 0) {
            append(&buffer, buffer_size, &required_size, " | ", 3);
        }
        required_size += snprintf(buffer, buffer_size - required_size,
                                  "0x%%08x", usage);
    } else {
        buffer[0] = 0;
    }
    return required_size;
}

/* End of automatically generated file. */
'''

key_type_from_curve_template = '''if (%(tester)s(type)) {
        append_with_curve(&buffer, buffer_size, &required_size,
                          "%(builder)s", %(builder_length)s,
                          PSA_KEY_TYPE_GET_CURVE(type));
    } else '''

algorithm_from_hash_template = '''if (%(tester)s(alg_without_padding)) {
        append_with_hash(&buffer, buffer_size, &required_size,
                         "%(builder)s", %(builder_length)s,
                         PSA_ALG_GET_HASH(alg_without_padding));
    } else '''

bit_test_template = '''\
    if (%(var)s & %(flag)s) {
        if (required_size != 0) {
            append(&buffer, buffer_size, &required_size, " | ", 3);
        }
        append(&buffer, buffer_size, &required_size, "%(flag)s", %(length)d);
        %(var)s ^= %(flag)s;
    }\
'''

class MacroCollector:
    def __init__(self):
        self.statuses = set()
        self.key_types = set()
        self.key_types_from_curve = {}
        self.ecc_curves = set()
        self.algorithms = set()
        self.hash_algorithms = set()
        self.block_cipher_padding_modes = set()
        self.algorithms_from_hash = {}
        self.key_usages = set()

    # "#define" followed by a macro name with either no parameters
    # or a single parameter. Grab the macro name in group 1, the
    # parameter name if any in group 2 and the definition in group 3.
    definition_re = re.compile(r'\s*#\s*define\s+(\w+)(?:\s+|\((\w+)\)\s*)(.+)(?:/[*/])?')

    def read_line(self, line):
        m = re.match(self.definition_re, line)
        if not m:
            return
        name, parameter, definition = m.groups()
        if name.endswith('_FLAG') or name.endswith('MASK'):
            # Macro only to build actual values
            return
        elif (name.startswith('PSA_ERROR_') or name == 'PSA_SUCCESS') \
           and not parameter:
            self.statuses.add(name)
        elif name.startswith('PSA_KEY_TYPE_') and not parameter:
            self.key_types.add(name)
        elif name.startswith('PSA_KEY_TYPE_') and parameter == 'curve':
            self.key_types_from_curve[name] = name[:13] + 'IS_' + name[13:]
        elif name.startswith('PSA_ECC_CURVE_') and not parameter:
            self.ecc_curves.add(name)
        elif name.startswith('PSA_ALG_BLOCK_CIPHER_PAD_') and not parameter:
            self.block_cipher_padding_modes.add(name)
        elif name.startswith('PSA_ALG_') and not parameter:
            if name in ['PSA_ALG_BLOCK_CIPHER_BASE',
                        'PSA_ALG_ECDSA_BASE',
                        'PSA_ALG_RSA_PKCS1V15_SIGN_BASE']:
                # Ad hoc skipping of duplicate names for some numerical values
                return
            self.algorithms.add(name)
            # Ad hoc detection of hash algorithms
            if re.search(r'0x010000[0-9A-Fa-f]{2}', definition):
                self.hash_algorithms.add(name)
        elif name.startswith('PSA_ALG_') and parameter == 'hash_alg':
            if name in ['PSA_ALG_DSA', 'PSA_ALG_ECDSA']:
                # A naming irregularity
                tester = name[:8] + 'IS_RANDOMIZED_' + name[8:]
            else:
                tester = name[:8] + 'IS_' + name[8:]
            self.algorithms_from_hash[name] = tester
        elif name.startswith('PSA_KEY_USAGE_') and not parameter:
            self.key_usages.add(name)
        else:
            # Other macro without parameter
            return

    def read_file(self, header_file):
        for line in header_file:
            self.read_line(line)

    def make_return_case(self, name):
        return 'case %(name)s: return "%(name)s";' % {'name': name}

    def make_append_case(self, name):
        template = ('case %(name)s: '
                    'append(&buffer, buffer_size, &required_size, "%(name)s", %(length)d); '
                    'break;')
        return template % {'name': name, 'length': len(name)}

    def make_inner_append_case(self, name):
        template = ('case %(name)s: '
                    'append(buffer, buffer_size, required_size, "%(name)s", %(length)d); '
                    'break;')
        return template % {'name': name, 'length': len(name)}

    def make_bit_test(self, var, flag):
        return bit_test_template % {'var': var,
                                    'flag': flag,
                                    'length': len(flag)}

    def make_status_cases(self):
        return '\n    '.join(map(self.make_return_case,
                                 sorted(self.statuses)))

    def make_ecc_curve_cases(self):
        return '\n    '.join(map(self.make_return_case,
                                 sorted(self.ecc_curves)))

    def make_key_type_cases(self):
        return '\n    '.join(map(self.make_append_case,
                                 sorted(self.key_types)))

    def make_key_type_from_curve_code(self, builder, tester):
        return key_type_from_curve_template % {'builder': builder,
                                               'builder_length': len(builder),
                                               'tester': tester}

    def make_key_type_code(self):
        d = self.key_types_from_curve
        make = self.make_key_type_from_curve_code
        return '\n        '.join([make(k, d[k]) for k in sorted(d.keys())])

    def make_hash_algorithm_cases(self):
        return '\n    '.join(map(self.make_return_case,
                                 sorted(self.hash_algorithms)))

    def make_padding_mode_cases(self):
        return '\n    '.join(map(self.make_inner_append_case,
                                 sorted(self.block_cipher_padding_modes)))

    def make_algorithm_cases(self):
        return '\n    '.join(map(self.make_append_case,
                                 sorted(self.algorithms)))

    def make_algorithm_from_hash_code(self, builder, tester):
        return algorithm_from_hash_template % {'builder': builder,
                                               'builder_length': len(builder),
                                               'tester': tester}

    def make_algorithm_code(self):
        d = self.algorithms_from_hash
        make = self.make_algorithm_from_hash_code
        return '\n        '.join([make(k, d[k]) for k in sorted(d.keys())])

    def make_key_usage_code(self):
        return '\n'.join([self.make_bit_test('usage', bit)
                          for bit in sorted(self.key_usages)])

    def write_file(self, output_file):
        data = {}
        data['status_cases'] = self.make_status_cases()
        data['ecc_curve_cases'] = self.make_ecc_curve_cases()
        data['key_type_cases'] = self.make_key_type_cases()
        data['key_type_code'] = self.make_key_type_code()
        data['hash_algorithm_cases'] = self.make_hash_algorithm_cases()
        data['padding_mode_cases'] = self.make_padding_mode_cases()
        data['algorithm_cases'] = self.make_algorithm_cases()
        data['algorithm_code'] = self.make_algorithm_code()
        data['key_usage_code'] = self.make_key_usage_code()
        output_file.write(output_template % data)

def generate_psa_constants(header_file_name, output_file_name):
    collector = MacroCollector()
    with open(header_file_name) as header_file:
        collector.read_file(header_file)
    temp_file_name = output_file_name + '.tmp'
    with open(temp_file_name, 'w') as output_file:
        collector.write_file(output_file)
    os.rename(temp_file_name, output_file_name)

if __name__ == '__main__':
    if not os.path.isdir('programs') and os.path.isdir('../programs'):
        os.chdir('..')
    generate_psa_constants('include/psa/crypto.h',
                           'programs/psa/psa_constant_names_generated.c')
