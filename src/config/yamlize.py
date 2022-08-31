from typing import get_type_hints
import inspect
import strictyaml as sl
import yaml

def yamlize(configurable_class):
    #define a new display method
    
    def convert_type_to_strictyaml(val):
        if val == int:
            return sl.Int()
        elif val == str:
            return sl.Str()
        elif val == float:
            return sl.Float()
        elif val == bool:
            return sl.Bool()
        else:
            raise ValueError(f"Type {val} could not be converted to StrictYAML type. Please add to the convert_type_to_strictyaml function.")
    
    init_types = get_type_hints(configurable_class.__init__)
    init_signature = inspect.signature(configurable_class.__init__)
    init_defaults = { k: v.default for k, v in init_signature.parameters.items() if v.default is not inspect.Parameter.empty}
    print(init_defaults)
    schema = {}
    for key, val in init_types.items():
        if key in init_defaults:
            key = sl.Optional(key, default=init_defaults[key])
        schema[key] =  convert_type_to_strictyaml(val)
    schema = sl.Map(schema)
    configurable_class.schema = schema
    
    def init_from_config_dict(cls, config):
        config_yamlized = yaml.dump(config)
        config_dict = sl.load(config_yamlized, schema)
        return cls(**config_dict)
    
    configurable_class.instantiate_from_config_dict = classmethod(init_from_config_dict)

    def init_from_config(cls, config_file_location):
        with open(config_file_location, 'r') as mf:
            yaml_str = mf.read()
        try:
            config_dict = sl.load(yaml_str, schema).data
        except Exception as e:
            raise ValueError(yaml_str, schema, e)
        try:
            return cls(**config_dict)
        except Exception as e:
            import logging
            logging.info(config_dict)
            print(config_dict)
            raise ValueError(config_dict)
    
    configurable_class.instantiate_from_config = classmethod(init_from_config)
    
    return configurable_class
